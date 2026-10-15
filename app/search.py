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
