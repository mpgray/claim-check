"""Model access: one OpenAI-compatible chat endpoint (LiteLLM or OpenRouter).

The same wire shape articles-ai's `OpenAICompatClient` speaks, so the fleet's
LiteLLM proxy works as-is. `FakeLLM` answers from fixtures so the UI and tests
run with no key and no network.
"""
import json
import re
from typing import Protocol

import httpx


class LLM(Protocol):
    def complete_json(self, system: str, user: str) -> dict: ...


class LLMError(RuntimeError):
    pass


def _lenient_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r'```(?:json)?\s*(.*?)```', text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end < start:
        raise LLMError('Model did not return JSON.')
    return json.loads(text[start:end + 1])


# Attribution, so OpenRouter's activity log says which app spent the money.
HEADERS = {'X-Title': 'Claim Check', 'HTTP-Referer': 'https://github.com/mpgray/claim-check'}


class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 90.0,
                 transport: httpx.BaseTransport | None = None):
        self.url = base_url.rstrip('/') + '/v1/chat/completions'
        self.api_key = api_key
        self.model = model
        self.client = httpx.Client(timeout=timeout, transport=transport)
        # Not every OpenRouter provider honours response_format. Learned once
        # per process: after a 400 that names it, stop sending it.
        self.json_mode = True

    def _post(self, system: str, user: str) -> httpx.Response:
        payload = {'model': self.model, 'temperature': 0, 'max_tokens': 4096,
                   'messages': [{'role': 'system', 'content': system},
                                {'role': 'user', 'content': user}]}
        if self.json_mode:
            payload['response_format'] = {'type': 'json_object'}
        return self.client.post(self.url, json=payload,
                                headers={'Authorization': f'Bearer {self.api_key}', **HEADERS})

    def complete_json(self, system: str, user: str) -> dict:
        try:
            resp = self._post(system, user)
            if resp.status_code == 400 and self.json_mode:
                self.json_mode = False
                resp = self._post(system, user)
            resp.raise_for_status()
            message = resp.json()['choices'][0]['message']
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise LLMError(f'Model call failed: {exc}') from exc
        content = message.get('content') or ''
        if not content:
            # Gemma 4 has an optional reasoning mode; a reply that is all
            # reasoning and no answer is a failure, not an empty verdict.
            raise LLMError('Model returned no answer text'
                           + (' (reasoning only).' if message.get('reasoning') else '.'))
        try:
            return _lenient_json(content)
        except (ValueError, LLMError) as exc:
            raise LLMError(f'Model returned unreadable JSON: {exc}') from exc


class FakeLLM:
    """Deterministic stand-in. Extraction picks sentences with figures;
    judging finds the claim's value in a page and quotes the sentence."""

    def complete_json(self, system: str, user: str) -> dict:
        if 'EXTRACT' in system:
            return {'claims': _fake_extract(user)}
        return _fake_judge(user)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]


def _fake_extract(user: str) -> list[dict]:
    body = user.split('ARTICLE:', 1)[-1]
    out = []
    for s in _sentences(body):
        m = re.search(r'\d[\d,.]*\s?%?', s)
        if m and len(s) >= 20:
            out.append({'span': s, 'kind': 'figure', 'value': m.group(0).strip(),
                        'query': s[:80]})
    return out


def _fake_judge(user: str) -> dict:
    claim = json.loads(user.split('CLAIM:', 1)[1].split('\nSOURCES:', 1)[0])
    sources = json.loads(user.split('SOURCES:', 1)[1])
    value = claim.get('value') or ''
    for src in sources:
        for s in _sentences(src['text']):
            if value and value in s:
                return {'verdict': 'supported', 'url': src['url'], 'quote': s,
                        'reason': 'The source states the same figure.'}
    return {'verdict': 'unverified', 'reason': 'No source states this figure.'}
