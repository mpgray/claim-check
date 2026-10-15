"""Probe the live model and search backend in one command. Run before a demo.

    uv run python -m scripts.check_backend

Checks that the chat model answers in JSON and that the search backend
actually searched (annotations present), and prints what it used.
"""
import sys
import time

from app import build_checker
from app.config import Config


def main() -> int:
    cfg = Config()
    if not (cfg.llm_base_url and cfg.llm_model):
        print('No model configured: set CC_OPENROUTER_API_KEY (or CC_LLM_*).')
        return 1
    checker = build_checker(cfg)
    ok = True
    t = time.monotonic()
    try:
        out = checker.llm.complete_json(
            'Return JSON {"ok": true, "model": "<your name>"} and nothing else.', 'ping')
        print(f'model  OK   {cfg.llm_model}  {time.monotonic() - t:.1f}s  {out}')
    except Exception as exc:
        ok = False
        print(f'model  FAIL {cfg.llm_model}: {exc}')
    t = time.monotonic()
    try:
        results = checker.search.search('federal minimum wage site:dol.gov')
        print(f'search OK   {cfg.search} ({cfg.search_model or cfg.llm_model})  '
              f'{len(results)} results  {time.monotonic() - t:.1f}s')
        for r in results[:3]:
            print('       ', r['url'])
        if cfg.search != 'none' and not results:
            ok = False
            print('search returned nothing for an easy query; treat as broken.')
    except Exception as exc:
        ok = False
        print(f'search FAIL {cfg.search}: {exc}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
