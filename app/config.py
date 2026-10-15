"""Runtime configuration, from the environment only.

Blank means off: no LLM key runs the offline fake (useful for UI work and the
test suite), no SearXNG URL means claims are judged only against the links the
article itself cites.
"""
import os
from dataclasses import dataclass, field


def _list(name: str) -> list[str]:
    return [d.strip().lower() for d in os.environ.get(name, '').split(',') if d.strip()]


@dataclass(frozen=True)
class Config:
    llm_base_url: str = field(default_factory=lambda: os.environ.get('CC_LLM_BASE_URL', ''))
    llm_api_key: str = field(default_factory=lambda: os.environ.get('CC_LLM_API_KEY', ''))
    llm_model: str = field(default_factory=lambda: os.environ.get('CC_LLM_MODEL', ''))
    searxng_url: str = field(default_factory=lambda: os.environ.get('CC_SEARXNG_URL', ''))
    # Tiering, as in articles-ai: who writes the rule beats who writes about it.
    official_domains: list[str] = field(default_factory=lambda: _list('CC_OFFICIAL_DOMAINS'))
    reference_domains: list[str] = field(default_factory=lambda: _list('CC_REFERENCE_DOMAINS'))
    max_claims: int = field(default_factory=lambda: int(os.environ.get('CC_MAX_CLAIMS', '15')))
    max_sources_per_claim: int = 3
    workers: int = 6
