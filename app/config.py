"""Runtime configuration, from the environment only.

Blank means off: no LLM key runs the offline fake (useful for UI work and the
test suite), no SearXNG URL means claims are judged only against the links the
article itself cites.
"""
import os
from dataclasses import dataclass, field


def _list(name: str) -> list[str]:
    return [d.strip().lower() for d in os.environ.get(name, '').split(',') if d.strip()]


OPENROUTER_URL = 'https://openrouter.ai/api'
# Gemma 4 31B instruct on OpenRouter. The dated slug
# `google/gemma-4-31b-it-20260402` pins the exact release if the alias moves.
DEFAULT_OPENROUTER_MODEL = 'google/gemma-4-31b-it'


def _openrouter_key() -> str:
    return os.environ.get('CC_OPENROUTER_API_KEY', '')


def _llm_base_url() -> str:
    return os.environ.get('CC_LLM_BASE_URL') or (OPENROUTER_URL if _openrouter_key() else '')


def _llm_model() -> str:
    return os.environ.get('CC_LLM_MODEL') or (DEFAULT_OPENROUTER_MODEL if _openrouter_key() else '')


def _search_backend() -> str:
    explicit = os.environ.get('CC_SEARCH', '').lower()
    if explicit:
        return explicit
    if os.environ.get('CC_SEARXNG_URL'):
        return 'searxng'
    return 'openrouter' if _openrouter_key() else 'none'


@dataclass(frozen=True)
class Config:
    llm_base_url: str = field(default_factory=_llm_base_url)
    llm_api_key: str = field(default_factory=lambda: os.environ.get('CC_LLM_API_KEY') or _openrouter_key())
    llm_model: str = field(default_factory=_llm_model)
    # searxng | openrouter | none
    search: str = field(default_factory=_search_backend)
    searxng_url: str = field(default_factory=lambda: os.environ.get('CC_SEARXNG_URL', ''))
    openrouter_api_key: str = field(default_factory=_openrouter_key)
    # Pinned SEPARATELY from the chat model. articles-ai learned this the hard
    # way: moving the chat model to one without web-plugin support took search
    # down silently, HTTP 200 and no results. Blank = use the chat model.
    search_model: str = field(default_factory=lambda: os.environ.get('CC_SEARCH_MODEL', ''))
    # Tiering, as in articles-ai: who writes the rule beats who writes about it.
    official_domains: list[str] = field(default_factory=lambda: _list('CC_OFFICIAL_DOMAINS'))
    reference_domains: list[str] = field(default_factory=lambda: _list('CC_REFERENCE_DOMAINS'))
    # Built-in tier lists (app.search) on top of the two above; CC_DEFAULT_TIERS=0 for only yours.
    default_tiers: bool = field(default_factory=lambda: os.environ.get('CC_DEFAULT_TIERS', '1') != '0')
    max_claims: int = field(default_factory=lambda: int(os.environ.get('CC_MAX_CLAIMS', '15')))
    max_sources_per_claim: int = field(default_factory=lambda: int(os.environ.get('CC_MAX_SOURCES', '3')))
    workers: int = 6
    # Reports (SQLite) and snapshots (PNG) live here.
    data_dir: str = field(default_factory=lambda: os.environ.get('CC_DATA_DIR', 'data'))
    # A finished report for the same URL younger than this is reused.
    cache_seconds: int = field(default_factory=lambda: int(os.environ.get('CC_CACHE_SECONDS', '3600')))
    # Page snapshots need Playwright's Chromium; off with CC_SNAPSHOTS=0.
    snapshots: bool = field(default_factory=lambda: os.environ.get('CC_SNAPSHOTS', '1') != '0')
