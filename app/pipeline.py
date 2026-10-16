"""article URL -> claims -> sources -> verdicts with receipts.

Every model output passes through code before it counts: spans through
`claims.validate`, verdicts through `verify.check`. The summary is counting.

`Checker.run` reports progress through `on_event(kind, report, data)` so a
background job can persist and stream the report as claims finish.
"""
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from app import claims as claims_mod
from app import verify
from app.config import Config
from app.fetch import FetchError, Page, article_passages, fetch_page, focused_excerpt
from app.llm import LLM, LLMError
from app.prompts import EXTRACT_SYSTEM, EXTRACT_USER, JUDGE_SYSTEM, JUDGE_USER
from app.search import Search, tier

CHUNK_CHARS = 30_000       # one extraction call
MAX_ARTICLE_CHARS = 200_000  # beyond this the article is not read, and the report says so


@dataclass
class Report:
    url: str
    title: str = ''
    passages: list[str] = field(default_factory=list)
    claims: list[claims_mod.Claim] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    cited_links: list[str] = field(default_factory=list)
    error: str = ''
    search_error: str = ''
    counts: dict = field(default_factory=dict)
    tiers: dict = field(default_factory=dict)
    date: str = ''             # the article's declared date
    proposed: int = 0          # claims the extractor proposed, before validation
    truncated_chars: int = 0   # article text past MAX_ARTICLE_CHARS, not read
    extract_errors: list[str] = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)
    status: str = 'running'    # running | done | error


def site(url: str) -> str:
    """Host without `www.`/mobile prefixes: the unit for "the article's own site"."""
    host = (urlsplit(url).hostname or '').lower()
    for prefix in ('www.', 'm.', 'mobile.', 'amp.'):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host.replace('.m.', '.')


def _cited_links(hrefs: list[str], base: str) -> list[str]:
    """Off-site links the article cites, absolute and de-duplicated."""
    own = site(base)
    out = []
    for href in hrefs:
        url = urljoin(base, href).split('#', 1)[0]
        if url.startswith(('http://', 'https://')) and site(url) != own and url not in out:
            out.append(url)
    return out


def chunk_passages(passages: list[str], size: int = CHUNK_CHARS,
                   cap: int = MAX_ARTICLE_CHARS) -> tuple[list[list[str]], int]:
    """Passages grouped into extraction chunks, and how many chars were left unread."""
    chunks: list[list[str]] = [[]]
    used, length = 0, 0
    total = sum(len(p) + 2 for p in passages)
    for passage in passages:
        cost = len(passage) + 2
        if used + cost > cap:
            break
        if length and length + cost > size:
            chunks.append([])
            length = 0
        chunks[-1].append(passage[:size])
        length += cost
        used += cost
    return [c for c in chunks if c], max(0, total - used)


def summarize(report: Report) -> None:
    counts = {v: 0 for v in verify.VERDICTS}
    tiers = {'official': 0, 'reference': 0, 'open': 0}
    for c in report.claims:
        if c.verdict in counts:
            counts[c.verdict] += 1
        if c.receipt:
            tiers[c.receipt['tier']] += 1
    report.counts, report.tiers = counts, tiers


def _noop(kind, report, data):
    pass


class Checker:
    def __init__(self, cfg: Config, llm: LLM, search: Search, fetch=fetch_page,
                 snapshotter=None):
        self.cfg, self.llm, self.search, self.fetch = cfg, llm, search, fetch
        self.snapshotter = snapshotter
        self._page_cache: dict[str, Page | None] = {}
        self._cache_lock = threading.Lock()
        self.search_error = ''

    def _page(self, url: str) -> Page | None:
        with self._cache_lock:
            if url in self._page_cache:
                return self._page_cache[url]
        try:
            page = self.fetch(url)
        except FetchError:
            page = None
        with self._cache_lock:
            self._page_cache.setdefault(url, page)
            if page:
                self._page_cache.setdefault(page.url, page)
        return page

    def page(self, url: str) -> Page | None:
        """A page this run already read, without fetching again."""
        with self._cache_lock:
            return self._page_cache.get(url)

    def run(self, url: str, on_event=None) -> Report:
        emit = on_event or _noop
        report = Report(url=url)
        try:
            page = self.fetch(url)
        except FetchError as exc:
            report.error, report.status = str(exc), 'error'
            emit('error', report, {'error': report.error})
            return report
        with self._cache_lock:
            self._page_cache[url] = self._page_cache[page.url] = page
        chunks, report.truncated_chars = chunk_passages(article_passages(page.passages))
        read = [p for chunk in chunks for p in chunk]
        body = '\n\n'.join(read)
        report.url, report.title, report.passages, report.date = page.url, page.title, read, page.date
        report.cited_links = _cited_links(page.links, page.url)
        emit('fetched', report, {'title': page.title})

        raw = self._extract(chunks, report)
        if not raw and report.extract_errors:
            report.error = f'Claim extraction failed: {report.extract_errors[0]}'
            report.status = 'error'
            emit('error', report, {'error': report.error})
            return report
        report.proposed = len(raw)
        report.claims, report.rejected = claims_mod.validate(body, raw, self.cfg.max_claims)
        report.missed = claims_mod.missed(body, report.claims)
        summarize(report)
        emit('claims', report, {'count': len(report.claims)})

        with ThreadPoolExecutor(self.cfg.workers) as pool:
            futures = {pool.submit(self._judge, c, report): c for c in report.claims}
            for fut in as_completed(futures):
                claim = futures[fut]
                try:
                    fut.result()
                except Exception as exc:  # a bug in one claim never sinks the report
                    claim.verdict, claim.reason = 'unverified', f'Checking failed: {exc}'[:300]
                summarize(report)
                emit('claim', report, {'id': claim.id})
        report.search_error = self.search_error
        summarize(report)

        if self.snapshotter:
            emit('snapshotting', report, {})
            try:
                self.snapshotter.capture(report, self)
            except Exception as exc:
                report.snapshot = {'error': f'Snapshots failed: {exc}'[:300]}
            emit('snapshots', report, {})
        report.status = 'done'
        emit('done', report, {})
        return report

    def _extract(self, chunks: list[list[str]], report: Report) -> list[dict]:
        total = sum(len(p) for chunk in chunks for p in chunk) or 1

        def one(chunk: list[str]) -> list[dict]:
            text = '\n\n'.join(chunk)
            # Each chunk asks for its share of the limit, so a long article's
            # claims are not all taken from its first screen.
            share = max(3, math.ceil(self.cfg.max_claims * len(text) / total) + 1)
            return self.llm.complete_json(
                EXTRACT_SYSTEM.replace('{limit}', str(share)),
                EXTRACT_USER.format(article_date=report.date or 'unknown', article=text)
            ).get('claims', []) or []

        results: list[list[dict]] = [[] for _ in chunks]
        with ThreadPoolExecutor(min(self.cfg.workers, len(chunks) or 1)) as pool:
            futures = {pool.submit(one, chunk): i for i, chunk in enumerate(chunks)}
            for fut in as_completed(futures):
                try:
                    results[futures[fut]] = fut.result()
                except LLMError as exc:
                    report.extract_errors.append(str(exc))
        # Interleave so the claim limit samples every chunk, not just the first.
        out, depth = [], max((len(r) for r in results), default=0)
        for i in range(depth):
            out += [r[i] for r in results if i < len(r) and isinstance(r[i], dict)]
        return out

    def _candidate_urls(self, claim: claims_mod.Claim, cited: list[str],
                        article_url: str) -> list[str]:
        urls: list[str] = []
        if claim.query:
            try:
                urls += [r['url'] for r in self.search.search(claim.query)]
            except Exception as exc:
                # A search outage leaves the cited-link path, but never
                # silently: the report says search was down, or a page of
                # "unverified" would look like an article nobody can support.
                self.search_error = f'Search failed: {exc}'[:300]
        # The article's own citations are leads too, but never the only lead
        # when search is available: a citation is what is being checked.
        urls += cited
        own = site(article_url)
        seen, out = set(), []
        for u in urls:
            if u in seen:
                continue
            seen.add(u)
            # The article cannot be its own receipt, nor can its publisher's
            # mirror of it: that is the claim quoting itself.
            if site(u) == own:
                claim.dropped_self += 1
                continue
            out.append(u)
        return out

    def _judge(self, claim: claims_mod.Claim, report: Report) -> None:
        if claim.kind == 'opinion':
            claim.verdict, claim.reason = 'opinion', 'Judgement or advice; not checked.'
            return
        pages: dict[str, str] = {}
        dates: dict[str, str] = {}
        sources = []
        for url in self._candidate_urls(claim, report.cited_links, report.url):
            if len(sources) >= self.cfg.max_sources_per_claim:
                break
            page = self._page(url)
            if not page or site(page.url) == site(report.url):
                continue
            pages[page.url] = page.text
            dates[page.url] = page.date
            sources.append({'url': page.url, 'title': page.title, 'date': page.date,
                            'kind': page.kind,
                            'text': focused_excerpt(page.passages, claim.span),
                            'tier': tier(page.url, self.cfg.official_domains,
                                         self.cfg.reference_domains)})
        claim.sources = [{k: s[k] for k in ('url', 'title', 'tier', 'date', 'kind')} for s in sources]
        if not sources:
            claim.verdict, claim.reason = 'unverified', 'No readable source was found.'
            return
        try:
            answer = self.llm.complete_json(
                JUDGE_SYSTEM,
                JUDGE_USER.format(
                    article_date=report.date or 'unknown',
                    claim=json.dumps({'span': claim.span, 'kind': claim.kind, 'value': claim.value}),
                    sources=json.dumps([{'url': s['url'], 'title': s['title'],
                                         'date': s['date'] or 'unknown', 'text': s['text']}
                                        for s in sources])))
        except LLMError as exc:
            claim.verdict, claim.reason = 'unverified', f'Judging failed: {exc}'
            return
        claim.verdict, receipt, claim.reason = verify.check(
            claim, answer, pages, article_date=report.date, source_dates=dates)
        if receipt:
            src = next(s for s in sources if s['url'] == receipt['url'])
            receipt['tier'], receipt['date'], receipt['kind'] = src['tier'], src['date'], src['kind']
        claim.receipt = receipt
