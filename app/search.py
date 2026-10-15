"""Search backends and source tiers.

Tiering is articles-ai's and game-db's idea: every source records whether it is
`official` (writes the rule, publishes the figure), `reference` (a curated
secondary source) or `open` (anything else). It is measurement, not advice —
the tier is decided by the host that answered, never by the model.
"""
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


def tier(url: str, official: list[str], reference: list[str]) -> str:
    host = urlsplit(url).hostname or ''
    if domain_matches(host, official):
        return 'official'
    if domain_matches(host, reference):
        return 'reference'
    return 'open'
