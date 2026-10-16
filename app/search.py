"""Search backends and source tiers.

Tiering is articles-ai's and game-db's idea: every source records whether it is
`official` (writes the rule, publishes the figure), `reference` (a curated
secondary source) or `open` (anything else). It is measurement, not advice —
the tier is decided by the host that answered, never by the model.
"""
import re
from typing import Protocol
from urllib.parse import urlsplit

import httpx


class Search(Protocol):
    def search(self, query: str) -> list[dict]: ...


class SearxngSearch:
    """A self-hosted SearXNG with `json` in `search.formats`. Public instances
    429 server-side searches (learned the hard way in articles-ai)."""

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout

    def search(self, query: str) -> list[dict]:
        resp = httpx.get(f'{self.base_url}/search', timeout=self.timeout,
                         params={'q': query, 'format': 'json', 'safesearch': 1})
        resp.raise_for_status()
        out = []
        for row in (resp.json() or {}).get('results') or []:
            url = (row.get('url') or '').strip()
            if url.startswith(('http://', 'https://')):
                out.append({'url': url, 'title': (row.get('title') or '').strip()})
        return out


class SearchError(RuntimeError):
    pass


class OpenRouterWebSearch:
    """OpenRouter's web plugin, used as a SEARCH step rather than fused into
    judging, so the URLs survive and every source is fetched and read by us.
    Ported from articles-ai `research.OpenRouterWebBackend`.
    """

    URL = 'https://openrouter.ai/api/v1/chat/completions'

    def __init__(self, api_key: str, model: str, max_results: int = 5,
                 timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
        self.api_key, self.model, self.max_results = api_key, model, max_results
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def search(self, query: str) -> list[dict]:
        resp = self.client.post(
            self.URL,
            headers={'Authorization': f'Bearer {self.api_key}',
                     'X-Title': 'Claim Check search',
                     'HTTP-Referer': 'https://github.com/mpgray/claim-check'},
            json={'model': self.model,
                  'plugins': [{'id': 'web', 'max_results': self.max_results}],
                  'max_tokens': 64,
                  'messages': [{'role': 'user', 'content':
                                f'Search for: {query}. Reply with the single word OK.'}]})
        resp.raise_for_status()
        message = ((resp.json().get('choices') or [{}])[0].get('message') or {})
        # A 200 with NO annotations key means the plugin did not run (a model
        # without web support) — an error, not "found nothing". `[]` is fine.
        if 'annotations' not in message:
            raise SearchError(f'{self.model} returned no web-search annotations: '
                              'the plugin did not run. Set CC_SEARCH_MODEL.')
        out = []
        for note in message.get('annotations') or []:
            cite = note.get('url_citation') or {}
            url = (cite.get('url') or '').strip()
            if url.startswith(('http://', 'https://')):
                out.append({'url': url, 'title': (cite.get('title') or '').strip()})
        return out


class NoSearch:
    """No backend configured: judge only against links the article cites."""

    def search(self, query: str) -> list[dict]:
        return []


def domain_matches(host: str, domains: list[str]) -> bool:
    """Label-boundary match: `archives.hud.gov` is HUD, `nothud.gov` is not."""
    host = (host or '').lower().strip('.')
    return any(host == d or host.endswith('.' + d) for d in domains)


# Built-in tiers. Still decided by the host alone, never by the model; the
# CC_OFFICIAL_DOMAINS / CC_REFERENCE_DOMAINS lists add to these, and
# CC_DEFAULT_TIERS=0 turns them off.
#
# official: writes the rule or publishes the figure. Governments everywhere
# (by suffix), intergovernmental bodies, and the bodies of record for figures
# articles often cite (charts, certifications, awards, central statistics).
OFFICIAL_SUFFIXES = [re.compile(p) for p in (
    r'\.gov$', r'\.mil$', r'\.int$', r'\.fed\.us$', r'\.(?:gov|gob|gouv|govt|go|gv)\.[a-z]{2}$',
    r'(?:^|\.)gc\.ca$', r'(?:^|\.)europa\.eu$', r'(?:^|\.)admin\.ch$', r'(?:^|\.)bund\.de$',
    r'(?:^|\.)gouv\.fr$', r'(?:^|\.)overheid\.nl$', r'(?:^|\.)parliament\.uk$')]
# Intergovernmental bodies and central banks, then bodies of record (from RECORD_START).
OFFICIAL_DOMAINS = [
    'un.org', 'worldbank.org', 'imf.org', 'oecd.org', 'wto.org', 'bis.org', 'ecb.europa.eu',
    'federalreserve.gov', 'stlouisfed.org', 'minneapolisfed.org', 'newyorkfed.org',
    'riaa.com', 'bpi.co.uk', 'ifpi.org', 'officialcharts.com', 'billboard.com', 'grammy.com',
    'oscars.org', 'nobelprize.org', 'olympics.com', 'fifa.com',
]
# reference: curated secondary sources with editorial standards: encyclopedias,
# universities and journals, wire services and papers of record, fact-checkers,
# and established statistics publishers.
REFERENCE_SUFFIXES = [re.compile(p) for p in (r'\.edu$', r'\.(?:ac|edu)\.[a-z]{2}$')]
REFERENCE_DOMAINS = [
    'wikipedia.org', 'britannica.com', 'doi.org', 'jstor.org', 'nature.com', 'science.org',
    'sciencedirect.com', 'springer.com', 'wiley.com', 'nejm.org', 'thelancet.com', 'bmj.com',
    'pnas.org', 'cell.com', 'arxiv.org', 'ssrn.com', 'nber.org', 'brookings.edu', 'rand.org',
    'pewresearch.org', 'ourworldindata.org', 'cbo.gov', 'epi.org', 'urban.org', 'kff.org',
    'reuters.com', 'apnews.com', 'afp.com', 'bbc.co.uk', 'bbc.com', 'npr.org', 'pbs.org',
    'nytimes.com', 'washingtonpost.com', 'wsj.com', 'ft.com', 'economist.com', 'bloomberg.com',
    'theguardian.com', 'latimes.com', 'usatoday.com', 'cbsnews.com', 'nbcnews.com', 'abcnews.go.com',
    'cnn.com', 'cnbc.com', 'politico.com', 'axios.com', 'theatlantic.com', 'newyorker.com',
    'time.com', 'forbes.com', 'fortune.com', 'propublica.org', 'theconversation.com',
    'snopes.com', 'politifact.com', 'factcheck.org', 'fullfact.org',
    'rollingstone.com', 'variety.com', 'hollywoodreporter.com', 'pollstar.com', 'allmusic.com',
    'boxofficemojo.com', 'the-numbers.com', 'espn.com',
]
RECORD_START = OFFICIAL_DOMAINS.index('riaa.com')
TIER_RANK = {'official': 0, 'reference': 1, 'open': 2}


_ARCHIVE = re.compile(r'^https?://(?:web\.)?archive\.org/web/[0-9a-z_*]+/(?P<url>.+)$', re.I)


def unarchive(url: str) -> str:
    """The original URL behind a Wayback Machine copy, else the URL itself.
    An archived BBC page is a BBC page for tiering and de-duplication."""
    m = _ARCHIVE.match(url or '')
    if not m:
        return url
    inner = m.group('url')
    return inner if inner.startswith(('http://', 'https://')) else 'http://' + inner


def tier(url: str, official: list[str], reference: list[str], defaults: bool = True) -> str:
    host = (urlsplit(unarchive(url)).hostname or '').lower().strip('.')
    if domain_matches(host, official) or (defaults and (
            domain_matches(host, OFFICIAL_DOMAINS) or any(p.search(host) for p in OFFICIAL_SUFFIXES))):
        return 'official'
    if domain_matches(host, reference) or (defaults and (
            domain_matches(host, REFERENCE_DOMAINS) or any(p.search(host) for p in REFERENCE_SUFFIXES))):
        return 'reference'
    return 'open'
