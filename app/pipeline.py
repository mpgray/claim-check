"""article URL -> claims -> sources -> verdicts with receipts.

Every model output passes through code before it counts: spans through
`claims.validate`, verdicts through `verify.check`. The summary is counting.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from app import claims as claims_mod
from app import verify
from app.config import Config
from app.fetch import FetchError, Page, article_passages, fetch_page, focused_excerpt
from app.llm import LLM, LLMError
from app.prompts import EXTRACT_SYSTEM, EXTRACT_USER, JUDGE_SYSTEM, JUDGE_USER
from app.search import Search, tier

MAX_ARTICLE_CHARS = 40_000


@dataclass
class Report:
    url: str
    title: str
    passages: list[str]
    claims: list[claims_mod.Claim]
    rejected: list[dict]
    missed: list[str]
    cited_links: list[str]
    error: str = ''
    counts: dict = field(default_factory=dict)
    tiers: dict = field(default_factory=dict)


def _cited_links(hrefs: list[str], base: str) -> list[str]:
    """Off-site links the article cites, absolute and de-duplicated."""
    host = urlsplit(base).hostname
    out = []
    for href in hrefs:
        url = urljoin(base, href).split('#', 1)[0]
        if url.startswith(('http://', 'https://')) and urlsplit(url).hostname != host and url not in out:
            out.append(url)
    return out


def summarize(report: Report) -> None:
    counts = {v: 0 for v in verify.VERDICTS}
    tiers = {'official': 0, 'reference': 0, 'open': 0}
    for c in report.claims:
        counts[c.verdict] = counts.get(c.verdict, 0) + 1
        if c.receipt:
            tiers[c.receipt['tier']] += 1
    report.counts, report.tiers = counts, tiers


class Checker:
    def __init__(self, cfg: Config, llm: LLM, search: Search, fetch=fetch_page):
        self.cfg, self.llm, self.search, self.fetch = cfg, llm, search, fetch
        self._page_cache: dict[str, Page | None] = {}

    def _page(self, url: str) -> Page | None:
        if url not in self._page_cache:
            try:
                self._page_cache[url] = self.fetch(url)
            except FetchError:
                self._page_cache[url] = None
        return self._page_cache[url]

    def run(self, url: str) -> Report:
        try:
            page = self.fetch(url)
        except FetchError as exc:
            return Report(url, '', [], [], [], [], [], error=str(exc))
        body = '\n\n'.join(article_passages(page.passages))[:MAX_ARTICLE_CHARS]
        cited = _cited_links(page.links, page.url)
        try:
            raw = self.llm.complete_json(
                EXTRACT_SYSTEM.replace('{limit}', str(self.cfg.max_claims)),
                EXTRACT_USER.format(article=body)).get('claims', [])
        except LLMError as exc:
            return Report(page.url, page.title, page.passages, [], [], [], cited,
                          error=f'Claim extraction failed: {exc}')
        kept, rejected = claims_mod.validate(body, raw, self.cfg.max_claims)
        report = Report(page.url, page.title, article_passages(page.passages), kept,
                        rejected, claims_mod.missed(body, kept), cited)
        with ThreadPoolExecutor(self.cfg.workers) as pool:
            list(pool.map(lambda c: self._judge(c, cited), kept))
        summarize(report)
        return report

    def _candidate_urls(self, claim: claims_mod.Claim, cited: list[str]) -> list[str]:
        urls: list[str] = []
        if claim.query:
            try:
                urls += [r['url'] for r in self.search.search(claim.query)]
            except Exception:  # a search outage leaves the cited-link path
                pass
        # The article's own citations are leads too, but never the only lead
        # when search is available: a citation is what is being checked.
        urls += cited
        seen, out = set(), []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def _judge(self, claim: claims_mod.Claim, cited: list[str]) -> None:
        if claim.kind == 'opinion':
            claim.verdict, claim.reason = 'opinion', 'Judgement or advice; not checked.'
            return
        pages: dict[str, str] = {}
        sources = []
        for url in self._candidate_urls(claim, cited):
            if len(sources) >= self.cfg.max_sources_per_claim:
                break
            page = self._page(url)
            if not page:
                continue
            excerpt = focused_excerpt(page.passages, claim.span)
            pages[page.url] = page.text
            sources.append({'url': page.url, 'title': page.title, 'text': excerpt,
                            'tier': tier(page.url, self.cfg.official_domains,
                                         self.cfg.reference_domains)})
        claim.sources = [{k: s[k] for k in ('url', 'title', 'tier')} for s in sources]
        if not sources:
            claim.verdict, claim.reason = 'unverified', 'No readable source was found.'
            return
        try:
            answer = self.llm.complete_json(
                JUDGE_SYSTEM,
                JUDGE_USER.format(
                    claim=json.dumps({'span': claim.span, 'kind': claim.kind, 'value': claim.value}),
                    sources=json.dumps([{k: s[k] for k in ('url', 'title', 'text')} for s in sources])))
        except LLMError as exc:
            claim.verdict, claim.reason = 'unverified', f'Judging failed: {exc}'
            return
        claim.verdict, receipt, claim.reason = verify.check(claim, answer, pages)
        if receipt:
            receipt['tier'] = next(s['tier'] for s in sources if s['url'] == receipt['url'])
        claim.receipt = receipt
