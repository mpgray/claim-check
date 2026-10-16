"""article URL -> claims -> sources -> verdicts with receipts.

Every model output passes through code before it counts: spans through
`claims.validate`, verdicts through `verify.check`. The summary is counting.

`Checker.run` reports progress through `on_event(kind, report, data)` so a
background job can persist and stream the report as claims finish.
"""
import dataclasses
import json
import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from app import claims as claims_mod
from app import inflation, verify
from app.config import Config
from app.fetch import FetchError, Page, article_passages, fetch_page, focused_excerpt
from app.llm import LLM, LLMError
from app.prompts import EXTRACT_SYSTEM, EXTRACT_USER, JUDGE_SYSTEM, JUDGE_USER
from app.search import TIER_RANK, Search, tier, unarchive

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
    back_matter_chars: int = 0 # reference lists and "see also" cut from the body
    extract_errors: list[str] = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)
    status: str = 'running'    # running | done | error
    subject: str = ''          # what the article is about, confirmed in its text
    subject_names: list[str] = field(default_factory=list)
    id: str = ''               # the stored report's id, when there is one


def site(url: str) -> str:
    """Host without `www.`/mobile prefixes: the unit for "the article's own site".
    An archived copy counts as the site it archives."""
    host = (urlsplit(unarchive(url)).hostname or '').lower()
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


def audit_citations(cites: list[str], sources: list[dict], receipt: dict | None,
                    verdict: str) -> list[dict]:
    """What each link in the claim's own passage turned out to say. No extra
    model calls: it is read from what judging already did."""
    out = []
    for url in cites:
        src = next((s for s in sources if url in (s.get('requested'), s['url'])), None)
        if receipt and src and receipt.get('url') == src['url']:
            status = {'supported': 'supports', 'contradicted': 'contradicts',
                      'outdated': 'outdated'}.get(verdict, 'read')
        elif src:
            status = 'read'          # read, but not what settled the claim
        else:
            status = 'unread'        # unreadable, or past the source limit
        out.append({'url': url, 'status': status, 'tier': src['tier'] if src else ''})
    return out


def merge_inflation(check: dict, verdict: str, reason: str, receipt: dict | None):
    """Fold a computed inflation check into the judge's verdict.

    A conversion that is wrong (explicit base year, more than 10% off) makes
    the claim contradicted, with the computation as its receipt. A correct one
    never upgrades the claim by itself: the facts around it still need a
    source. An unsure one only adds its explanation.
    """
    status = check.get('status')
    if status == 'wrong':
        if verdict == 'contradicted' and receipt:
            return verdict, f"{reason} The inflation conversion is also wrong: {check['detail']}", receipt
        computed = {'url': check['url'], 'link': check['url'], 'quote': check['detail'],
                    'tier': 'official', 'kind': 'computed', 'as_of': '', 'date': ''}
        return 'contradicted', f"The inflation conversion is wrong: {check['detail']}", computed
    if status == 'ok':
        if verdict == 'supported':
            return verdict, f'{reason} The inflation conversion checks out against BLS CPI data.', receipt
        return verdict, (f'{reason} The inflation conversion checks out against BLS CPI data; '
                         'the other facts were not confirmed by a source.').strip(), receipt
    return verdict, f"{reason} Inflation: {check.get('detail', '')}".strip(), receipt


class Checker:
    def __init__(self, cfg: Config, llm: LLM, search: Search, fetch=fetch_page,
                 snapshotter=None):
        self.cfg, self.llm, self.search, self.fetch = cfg, llm, search, fetch
        self.snapshotter = snapshotter
        self.cpi_loader = lambda: inflation.load_cpi(cfg.data_dir)
        self._page_cache: dict[str, Page | None] = {}
        self._cache_lock = threading.Lock()
        # Held while a claim's result is written and while an event handler
        # reads the report, so a saved report never has half a verdict.
        self.lock = threading.RLock()
        self._link_context: list[tuple[str, str, int]] = []
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

    def run(self, url: str, on_event=None, report_id: str = '') -> Report:
        handler = on_event or _noop

        def emit(kind, report, data):
            with self.lock:
                handler(kind, report, data)
        report = Report(url=url, id=report_id)
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
        report.back_matter_chars = getattr(page, 'back_matter_chars', 0)
        report.cited_links = _cited_links(page.links, page.url)
        self._link_context = [(urljoin(page.url, h).split('#', 1)[0], ctx, off)
                              for h, ctx, off in page.link_context]
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
                    with self.lock:
                        claim.verdict, claim.reason = 'unverified', f'Checking failed: {exc}'[:300]
                with self.lock:
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

        def one(chunk: list[str]) -> dict:
            text = '\n\n'.join(chunk)
            # Each chunk asks for its share of the limit, so a long article's
            # claims are not all taken from its first screen.
            share = max(3, math.ceil(self.cfg.max_claims * len(text) / total) + 1)
            return self.llm.complete_json(
                EXTRACT_SYSTEM.replace('{limit}', str(share)),
                EXTRACT_USER.format(title=report.title or 'unknown',
                                    article_date=report.date or 'unknown', article=text)) or {}

        answers: list[dict] = [{} for _ in chunks]
        with ThreadPoolExecutor(min(self.cfg.workers, len(chunks) or 1)) as pool:
            futures = {pool.submit(one, chunk): i for i, chunk in enumerate(chunks)}
            for fut in as_completed(futures):
                try:
                    answers[futures[fut]] = fut.result()
                except LLMError as exc:
                    report.extract_errors.append(str(exc))
        body = ' '.join(p for chunk in chunks for p in chunk)
        report.subject, report.subject_names = claims_mod.validate_subject(
            answers, report.title, body)
        results = [a.get('claims') or [] for a in answers]
        # Interleave so the claim limit samples every chunk, not just the first.
        out, depth = [], max((len(r) for r in results), default=0)
        for i in range(depth):
            out += [r[i] for r in results if i < len(r) and isinstance(r[i], dict)]
        return out

    def _own_citations(self, claim: claims_mod.Claim, cited: list[str]) -> list[str]:
        """Links the article attaches to this claim: does the citation say that?

        Footnotes right after the claim's words come first, then links inside
        them. If there are neither, the next footnote cluster after the claim
        (a footnote covers the sentences before it), else every link in the
        claim's passage.
        """
        span = claims_mod.norm(claim.span)
        if not span:
            return []
        after, inside, later, passage = [], [], [], []
        for url, ctx, off in self._link_context:
            if url not in cited:
                continue
            flat = claims_mod.norm(ctx)
            at = flat.find(span[:120])
            if at < 0:
                continue
            end = at + len(span)
            if end - 2 <= off <= end + 60:
                after.append(url)
            elif at <= off < end:
                inside.append(url)
            elif off > end:
                later.append((off, url))
            else:
                passage.append(url)
        if later:
            first = min(o for o, _ in later)
            later = [u for o, u in sorted(later) if o <= first + 60]
        ordered = after + inside or later or passage
        seen: set = set()
        return [u for u in ordered if not (u in seen or seen.add(u))]

    def _tier(self, url: str) -> str:
        return tier(url, self.cfg.official_domains, self.cfg.reference_domains,
                    self.cfg.default_tiers)

    def _search(self, query: str) -> list[str]:
        """Search result URLs, sources of record first; [] on an outage (reported)."""
        try:
            found = [r['url'] for r in self.search.search(query)]
        except Exception as exc:
            # A search outage leaves the cited-link path, but never
            # silently: the report says search was down, or a page of
            # "unverified" would look like an article nobody can support.
            self.search_error = f'Search failed: {exc}'[:300]
            return []
        # Sources of record are read first; the source limit is small.
        return sorted(found, key=lambda u: TIER_RANK[self._tier(u)])

    @staticmethod
    def anchored(query: str, report: Report) -> str:
        """The claim's query, naming the article's subject. A sentence read
        alone ("Undermining is a foundation piece losing substrate contact")
        searches every game ever made unless the query says which one."""
        if not report.subject or not query or claims_mod.mentions(query, report.subject_names):
            return query
        return f'{report.subject} {query}'

    def _candidate_urls(self, claim: claims_mod.Claim, cited: list[str],
                        report: Report) -> list[str]:
        own_cites = self._own_citations(claim, cited)
        # The claim's own citations are read first (an original and its
        # archived copy are alternates); search leads come next, so the
        # citation is checked rather than trusted.
        urls: list[str] = own_cites[:4]
        if claim.query:
            urls += self._search(self.anchored(claim.query, report))
        # The article's own citations are leads too, but never the only lead
        # when search is available: a citation is what is being checked.
        urls += own_cites[4:] + cited
        return self._not_self(urls, claim, report.url)

    @staticmethod
    def _not_self(urls: list[str], claim: claims_mod.Claim, article_url: str) -> list[str]:
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
        check, value = self._inflation(claim, report)
        verdict, reason, receipt, sources = self._decide(claim, report, value)
        if check:
            verdict, reason, receipt = merge_inflation(check, verdict, reason, receipt)
        citations = audit_citations(self._own_citations(claim, report.cited_links),
                                    sources, receipt, verdict)
        with self.lock:
            claim.sources, claim.receipt = sources, receipt
            claim.verdict, claim.reason = verdict, reason
            claim.checks = [check] if check else []
            claim.citations = citations

    def _inflation(self, claim: claims_mod.Claim, report: Report) -> tuple[dict | None, str | None]:
        """(computed check, value to judge instead) for a claim with an
        inflation conversion in it; (None, None) otherwise."""
        if claim.kind == 'opinion':
            return None, None
        conv = inflation.find(claim.span)
        if not conv:
            return None, None
        inflation.infer_base_year(conv, claim.span, report.passages)
        try:
            check = inflation.check(conv, self.cpi_loader())
        except Exception as exc:
            check = {'kind': 'inflation', 'status': 'unsure', 'url': inflation.SOURCE_URL,
                     'detail': f'The inflation conversion could not be computed: {exc}'[:300]}
        # The judge cannot find a source restating the adjusted figure, so the
        # figure it is held to becomes the original amount.
        value = None
        digits = lambda t: claims_mod.norm(t).replace(',', '')
        if claim.value and digits(claim.value) in digits(conv.adjusted_text):
            value = re.sub(r'^(US)?\$', '', conv.amount_text.split()[0])
        return check, value

    def _decide(self, claim: claims_mod.Claim, report: Report, value: str | None = None):
        """(verdict, reason, receipt, sources) for one claim; writes nothing."""
        if claim.kind == 'opinion':
            return 'opinion', 'Judgement or advice; not checked.', None, []
        pages: dict[str, str] = {}
        dates: dict[str, str] = {}
        sources = []
        own = set(self._own_citations(claim, report.cited_links))
        state = {'own_read': 0, 'keys': set(), 'tried': set()}

        def read(urls):
            for url in urls:
                if len(sources) >= self.cfg.max_sources_per_claim:
                    return
                key = unarchive(url).split('://', 1)[-1].rstrip('/')
                if url in state['tried'] or key in state['keys'] or (url in own and state['own_read'] >= 2):
                    continue    # tried already, the archived twin of a page read, or enough citations
                state['tried'].add(url)
                page = self._page(url)
                if not page or site(page.url) == site(report.url):
                    continue
                # A page that never names the article's subject is about
                # something else (another game's wiki, a soil-engineering
                # paper), however well it matches the words. The claim's own
                # citations are exempt: the article chose them.
                if (report.subject_names and url not in own
                        and not claims_mod.mentions(f'{page.title} {page.text}', report.subject_names)):
                    claim.dropped_offtopic += 1
                    continue
                state['keys'].add(key)
                state['own_read'] += url in own
                pages[page.url] = page.text
                dates[page.url] = page.date
                sources.append({'url': page.url, 'requested': url, 'title': page.title,
                                'date': page.date, 'kind': page.kind,
                                'text': focused_excerpt(page.source_passages, claim.span),
                                'tier': self._tier(page.url)})

        read(self._candidate_urls(claim, report.cited_links, report))
        if len(sources) < self.cfg.max_sources_per_claim and report.subject and claim.query \
                and claim.dropped_offtopic:
            # Off-topic results crowded out the source limit: search once more
            # with the subject as an exact phrase.
            read(self._not_self(self._search(f'"{report.subject}" {claim.query}'), claim, report.url))
        listed = [{k: s[k] for k in ('url', 'requested', 'title', 'tier', 'date', 'kind')} for s in sources]
        if not sources:
            return 'unverified', 'No readable source was found.', None, listed
        try:
            answer = self.llm.complete_json(
                JUDGE_SYSTEM,
                JUDGE_USER.format(
                    article_date=report.date or 'unknown',
                    claim=json.dumps({'span': claim.span, 'kind': claim.kind,
                                      'value': value or claim.value}),
                    sources=json.dumps([{'url': s['url'], 'title': s['title'],
                                         'date': s['date'] or 'unknown', 'text': s['text']}
                                        for s in sources])))
        except LLMError as exc:
            return 'unverified', f'Judging failed: {exc}', None, listed
        judged = dataclasses.replace(claim, value=value) if value else claim
        verdict, receipt, reason = verify.check(
            judged, answer, pages, article_date=report.date, source_dates=dates)
        if receipt:
            src = next(s for s in sources if s['url'] == receipt['url'])
            receipt['tier'], receipt['date'], receipt['kind'] = src['tier'], src['date'], src['kind']
        return verdict, reason, receipt, listed
