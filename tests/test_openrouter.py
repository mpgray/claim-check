"""OpenRouter + Gemma 4 wiring, with no network (httpx.MockTransport)."""
import json

import httpx
import pytest

from app.config import DEFAULT_OPENROUTER_MODEL, Config
from app.llm import LLMError, OpenAICompatLLM
from app.search import OpenRouterWebSearch, SearchError


def test_openrouter_key_alone_selects_gemma_4_and_web_search(monkeypatch):
    for k in ('CC_LLM_BASE_URL', 'CC_LLM_MODEL', 'CC_LLM_API_KEY', 'CC_SEARCH', 'CC_SEARXNG_URL'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('CC_OPENROUTER_API_KEY', 'sk-test')
    cfg = Config()
    assert cfg.llm_base_url == 'https://openrouter.ai/api'
    assert cfg.llm_model == DEFAULT_OPENROUTER_MODEL == 'google/gemma-4-31b-it'
    assert cfg.llm_api_key == 'sk-test' and cfg.search == 'openrouter'


def _chat(content, **message):
    return {'choices': [{'message': {'content': content, **message}}]}


def test_json_mode_rejected_is_retried_without_it():
    """A provider that 400s on response_format must not fail every call."""
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append('response_format' in body)
        if 'response_format' in body:
            return httpx.Response(400, json={'error': 'response_format unsupported'})
        return httpx.Response(200, json=_chat('```json\n{"ok": true}\n```'))

    llm = OpenAICompatLLM('https://openrouter.ai/api', 'k', 'google/gemma-4-31b-it',
                          transport=httpx.MockTransport(handler))
    assert llm.complete_json('s', 'u') == {'ok': True}
    assert llm.complete_json('s', 'u') == {'ok': True}
    assert seen == [True, False, False]


def test_reasoning_only_reply_is_an_error_not_an_empty_answer():
    llm = OpenAICompatLLM('https://x', 'k', 'm', transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=_chat('', reasoning='thinking…'))))
    with pytest.raises(LLMError, match='reasoning only'):
        llm.complete_json('s', 'u')


def test_web_search_reads_annotations():
    def handler(request):
        assert json.loads(request.content)['plugins'][0]['id'] == 'web'
        return httpx.Response(200, json=_chat('OK', annotations=[
            {'type': 'url_citation', 'url_citation': {'url': 'https://www.dol.gov/wage', 'title': 'Wage'}}]))
    s = OpenRouterWebSearch('k', 'google/gemma-4-31b-it', transport=httpx.MockTransport(handler))
    assert s.search('q') == [{'url': 'https://www.dol.gov/wage', 'title': 'Wage'}]


def test_missing_annotations_means_the_plugin_did_not_run():
    """HTTP 200 with no annotations key is a search that silently did not run."""
    s = OpenRouterWebSearch('k', 'm', transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=_chat('OK'))))
    with pytest.raises(SearchError):
        s.search('q')


def test_empty_annotations_is_a_real_empty_result():
    s = OpenRouterWebSearch('k', 'm', transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=_chat('OK', annotations=[]))))
    assert s.search('q') == []
