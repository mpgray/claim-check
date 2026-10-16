"""Snapshots: the article and every receipt source, rendered and frozen.

After judging, a headless Chromium opens the article and paints each claim in
its verdict colour, then opens each receipt's source and paints the quote.
Each gets a full-page PNG and a tight crop per claim. A live link can change
or vanish; the image shows what the page said when it was checked.

Highlights only mark text the pipeline already verified (claim spans held to
the article, quotes held to the source). The browser finds them with the
same normalisation as `claims.norm`; a miss is recorded as `found: False`
and shown as such, never approximated.

The browser goes through the same public-address rule as `fetch.fetch_page`:
every request, including subresources, is refused if its host resolves to a
private, loopback or link-local address.
"""
import asyncio
import functools
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from app.fetch import FetchError, _check_public

HIGHLIGHT_JS = Path(__file__).parent / 'static' / 'highlight.js'
COLOURS = {
    'supported': 'rgba(52, 199, 89, .40)',
    'contradicted': 'rgba(255, 59, 48, .38)',
    'outdated': 'rgba(255, 159, 10, .45)',
    'unverified': 'rgba(142, 142, 147, .35)',
    'opinion': 'rgba(94, 129, 255, .25)',
    'pending': 'rgba(142, 142, 147, .25)',
    'quote': 'rgba(255, 214, 10, .60)',
}
VIEWPORT = {'width': 1280, 'height': 900}
MAX_IMAGE_HEIGHT = 12_000     # under Chromium's texture limit; taller pages get a window
NAV_TIMEOUT_MS = 20_000
TOTAL_TIMEOUT_S = 150
PAGE_TIMEOUT_S = 60
PARALLEL_SOURCES = 4
BLOCKED_TYPES = ('media', 'websocket', 'eventsource')


@functools.lru_cache(maxsize=1024)
def _public_host(host: str) -> bool:
    try:
        _check_public(host)
        return True
    except FetchError:
        return False


def allowed(url: str) -> bool:
    """May the snapshot browser load this URL?"""
    parts = urlsplit(url)
    if parts.scheme in ('data', 'blob', 'about'):
        return True
    if parts.scheme not in ('http', 'https'):
        return False
    return _public_host(parts.hostname or '')


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


class Snapshotter:
    """`html_for(url)` replaces navigation with fixed HTML (tests only)."""

    def __init__(self, root: str | Path, html_for=None):
        self.root = Path(root)
        self.html_for = html_for

    def capture(self, report, checker=None) -> None:
        rid = report.id or secrets.token_hex(6)
        out = self.root / rid
        out.mkdir(parents=True, exist_ok=True)
        report.snapshot = {'captured_at': _now()}
        asyncio.run(self._capture(report, out, checker))

    async def _capture(self, report, out: Path, checker) -> None:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                context = await browser.new_context(viewport=VIEWPORT, device_scale_factor=1,
                                                    service_workers='block')
                context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
                await context.route('**/*', self._route)
                try:
                    await asyncio.wait_for(self._all(context, report, out, checker), TOTAL_TIMEOUT_S)
                except asyncio.TimeoutError:
                    report.snapshot['error'] = 'Some snapshots timed out and were skipped.'
            finally:
                await browser.close()

    @staticmethod
    async def _route(route):
        req = route.request
        if req.resource_type in BLOCKED_TYPES or not await asyncio.to_thread(allowed, req.url):
            await route.abort()
        else:
            await route.continue_()

    async def _all(self, context, report, out: Path, checker) -> None:
        by_url: dict[str, list] = {}
        for c in report.claims:
            if c.receipt and c.receipt.get('kind') != 'pdf':
                by_url.setdefault(c.receipt['url'], []).append(c)
        gate = asyncio.Semaphore(PARALLEL_SOURCES)

        async def article():
            try:
                await asyncio.wait_for(self._article(context, report, out, checker), PAGE_TIMEOUT_S)
            except asyncio.TimeoutError:
                report.snapshot['article_error'] = 'The article took too long to render; no snapshot.'

        async def source(url, claims):
            async with gate:
                try:
                    await asyncio.wait_for(self._source(context, url, claims, out, checker),
                                           PAGE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    for c in claims:
                        self._set(checker, c, source_error='The source took too long to render; no image.')

        # The article and the sources render side by side: one slow, very
        # long page must not use up the time the receipts need.
        await asyncio.gather(article(), *(source(u, cs) for u, cs in by_url.items()))

    async def _open(self, context, url: str):
        page = await context.new_page()
        if self.html_for:
            await page.set_content(self.html_for(url), wait_until='domcontentloaded')
        else:
            await page.goto(url, wait_until='domcontentloaded')
            try:
                await page.wait_for_load_state('networkidle', timeout=5000)
            except Exception:
                pass      # busy pages never go idle; the text is there by now
        await page.add_script_tag(path=str(HIGHLIGHT_JS))
        await page.evaluate('window.__cc.unclutter()')
        return page

    @staticmethod
    async def _highlight(page, items: list[dict]) -> dict:
        marks = await page.evaluate('([items, colours]) => window.__cc.highlight(items, colours)',
                                    [items, COLOURS])
        if 'error' in marks:
            raise RuntimeError(marks['error'])
        for m in marks.values():
            # A zero-size range is text that is not actually on screen.
            if m.get('found') and not (m['rect']['w'] > 0 and m['rect']['h'] > 0):
                m['found'] = False
        return marks

    @staticmethod
    async def _full(page, rects: list[dict], path: Path) -> bool:
        """Full-page PNG. Returns True if the page was too tall and only the
        window around the highlights was captured.

        The viewport is resized to the capture window and screenshotted, not
        `full_page=True`: on a 70,000 px page that call re-lays out the whole
        document and takes ~30 s. Run this after the crops, since it moves
        the viewport.
        """
        height = int(await page.evaluate('window.__cc.height()'))
        top, size = 0, min(height, MAX_IMAGE_HEIGHT)
        if height > MAX_IMAGE_HEIGHT and rects:
            lo = min(r['y'] for r in rects)
            hi = max(r['y'] + r['h'] for r in rects)
            top = int(max(0, min(lo - 400, (lo + hi) / 2 - MAX_IMAGE_HEIGHT / 2,
                                 height - MAX_IMAGE_HEIGHT)))
        await page.set_viewport_size({'width': VIEWPORT['width'], 'height': max(size, 200)})
        await page.evaluate('y => window.scrollTo(0, y)', top)
        await page.screenshot(path=str(path))
        await page.set_viewport_size(VIEWPORT)
        return height > MAX_IMAGE_HEIGHT

    @staticmethod
    async def _crop(page, item_id: str, path: Path) -> bool:
        """A crop around one highlight, from the viewport after scrolling to
        it (fast at any page height). False if it is not on screen."""
        rect = await page.evaluate('id => window.__cc.focus(id)', item_id)
        if not rect or rect['w'] <= 0 or rect['h'] <= 0:
            return False
        vw = await page.evaluate('document.documentElement.clientWidth') or VIEWPORT['width']
        vh = VIEWPORT['height']
        w = min(vw, max(rect['w'] + 80, 720))
        x = min(max(0, rect['x'] + rect['w'] / 2 - w / 2), max(0, vw - w))
        y = max(0, rect['y'] - 60)
        h = min(rect['h'] + 120, vh - y)
        if h <= 20 or y >= vh:
            return False
        await page.screenshot(path=str(path), clip={'x': x, 'y': y, 'width': w, 'height': h})
        return True

    @staticmethod
    def _set(checker, claim, **values):
        if checker is not None:
            with checker.lock:
                claim.snapshot = {**(claim.snapshot or {}), **values}
        else:
            claim.snapshot = {**(claim.snapshot or {}), **values}

    async def _article(self, context, report, out: Path, checker) -> None:
        if not report.claims:
            return
        try:
            page = await self._open(context, report.url)
        except Exception as exc:
            report.snapshot['article_error'] = f'Could not render the article: {exc}'.split('\n')[0][:200]
            return
        try:
            items = [{'id': f'c{c.id}', 'text': c.span, 'cls': c.verdict} for c in report.claims]
            marks = await self._highlight(page, items)
            found = [m['rect'] for m in marks.values() if m.get('found')]
            if not found:
                # Nothing the fetch read is on this page: the browser was
                # served something else (a bot check, a consent wall). An image
                # of that would look like evidence; say what happened instead.
                title = (await page.title())[:120]
                report.snapshot['article_error'] = (
                    'The browser was served a different page than the fetch'
                    + (f' ("{title}")' if title else '') + ', so there is no article snapshot.')
                for c in report.claims:
                    self._set(checker, c, article_found=False)
                return
            for c in report.claims:
                m = marks.get(f'c{c.id}') or {}
                if not m.get('found'):
                    self._set(checker, c, article_found=False)
                    continue
                name = f'article-{c.id}.png'
                if await self._crop(page, f'c{c.id}', out / name):
                    self._set(checker, c, article_found=True, article_crop=name,
                              article_full='article.png', article_exact=m.get('exact', True))
                else:
                    self._set(checker, c, article_found=False)
            await page.evaluate('window.__cc.focus(null)')
            report.snapshot['article_partial'] = await self._full(page, found, out / 'article.png')
            report.snapshot['article_full'] = 'article.png'
            report.snapshot['article_found'] = len(found)
        except Exception as exc:
            report.snapshot['article_error'] = f'Article snapshot failed: {exc}'.split('\n')[0][:200]
        finally:
            await page.close()

    async def _source(self, context, url: str, claims: list, out: Path, checker) -> None:
        first = claims[0].id
        try:
            page = await self._open(context, url)
        except Exception as exc:
            for c in claims:
                self._set(checker, c, source_error=f'Could not render the source: {exc}'.split('\n')[0][:200])
            return
        try:
            items = [{'id': f'q{c.id}', 'text': c.receipt['quote'], 'cls': 'quote'} for c in claims]
            marks = await self._highlight(page, items)
            found = [m['rect'] for m in marks.values() if m.get('found')]
            if not found:
                title = (await page.title())[:120]
                for c in claims:
                    self._set(checker, c, source_found=False, source_error=(
                        'The quote was verified in the fetched page, but the browser was served a '
                        'different page' + (f' ("{title}")' if title else '')
                        + ', so there is no image.'))
                return
            full = f'source-{first}.png'
            captured = _now()
            for c in claims:
                m = marks.get(f'q{c.id}') or {}
                values = {'source_full': full, 'captured_at': captured,
                          'source_found': bool(m.get('found'))}
                if m.get('found'):
                    crop = f'source-{c.id}-crop.png'
                    if await self._crop(page, f'q{c.id}', out / crop):
                        values['source_crop'] = crop
                    else:
                        values['source_found'] = False
                self._set(checker, c, **values)
            await page.evaluate('window.__cc.focus(null)')
            partial = await self._full(page, found, out / full)
            for c in claims:
                self._set(checker, c, source_partial=partial)
        except Exception as exc:
            for c in claims:
                self._set(checker, c, source_error=f'Source snapshot failed: {exc}'.split('\n')[0][:200])
        finally:
            await page.close()
