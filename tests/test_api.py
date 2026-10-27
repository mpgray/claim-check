"""The API a publisher builds on: drafts, async checks, signed callbacks,
an access token, embedding, and the JSON schema version."""
import json

import httpx

from app import callback, create_app
from app.config import Config
from app.llm import FakeLLM
from app.pipeline import Checker
from app.store import Store
from tests.test_pipeline import ARTICLE_HTML, ARTICLE_URL, SOURCE_URL, FakeSearch, fake_fetch


def _client(tmp_path, **cfg):
    checker = Checker(Config(), FakeLLM(), FakeSearch(), fetch=fake_fetch)
    app = create_app(checker_factory=lambda: checker, store=Store(tmp_path / 'db.sqlite3'),
                     sync_jobs=True, cfg=Config(data_dir=str(tmp_path), snapshots=False, **cfg))
    return app.test_client()


def test_a_draft_is_checked_without_fetching_it(tmp_path):
    client = _client(tmp_path)
    resp = client.post('/api/checks', json={'html': ARTICLE_HTML, 'site': 'blog.example',
                                            'title': 'Draft: wage facts', 'date': '2026-10-01'})
    assert resp.status_code == 202
    body = resp.get_json()
    assert body['schema_version'] == 1 and body['report'].endswith(f"/r/{body['id']}")
    data = client.get(f"/api/r/{body['id']}").get_json()
    assert data['status'] == 'done' and data['url'] == 'draft:blog.example'
    assert data['title'] == 'Draft: wage facts' and data['date'] == '2026-10-01'
    assert data['counts']['supported'] == 1 and data['context'] == {'site': 'blog.example'}
    assert data['schema_version'] == 1


def test_a_plain_text_draft_and_a_url_check_both_start(tmp_path):
    client = _client(tmp_path)
    text = 'The federal minimum wage is $7.25 per hour, according to the Labor Department.'
    rid = client.post('/api/checks', json={'text': text}).get_json()['id']
    assert client.get(f'/api/r/{rid}').get_json()['status'] == 'done'
    rid = client.post('/api/checks', json={'url': ARTICLE_URL,
                                           'context': {'exclude_domains': ['x.example']}}).get_json()['id']
    data = client.get(f'/api/r/{rid}').get_json()
    assert data['url'] == ARTICLE_URL and data['context']['exclude_domains'] == ['x.example']


def test_bad_check_requests_are_refused(tmp_path):
    client = _client(tmp_path)
    assert client.post('/api/checks', data='x').status_code == 400
    assert client.post('/api/checks', json={}).status_code == 400
    assert client.post('/api/checks', json={'html': '<p></p>'}).status_code == 400
    # A callback needs a secret to sign with.
    assert client.post('/api/checks', json={'url': ARTICLE_URL,
                                            'callback_url': 'https://hooks.example/cc'}).status_code == 400


def test_callbacks_go_only_to_public_or_listed_hosts():
    assert callback.refused('ftp://x.example/', []) == 'callback_url must be an http(s) address.'
    assert 'not a public address' in callback.refused('http://127.0.0.1:9/cb', [])
    assert callback.refused('http://127.0.0.1:9/cb', ['127.0.0.1']) == ''


def test_a_callback_is_signed_and_its_delivery_recorded(tmp_path, monkeypatch):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200)
    real = callback.deliver
    monkeypatch.setattr('app.callback.deliver', lambda url, body, secret, hosts: real(
        url, body, secret, hosts, client=httpx.Client(transport=httpx.MockTransport(handler))))
    client = _client(tmp_path, webhook_secret='s3cret', callback_hosts=['receiver.internal'])
    rid = client.post('/api/checks', json={'url': ARTICLE_URL,
                                           'callback_url': 'http://receiver.internal/cc'}).get_json()['id']
    assert len(sent) == 1
    req = sent[0]
    assert callback.verify('s3cret', req.content, req.headers[callback.TIMESTAMP_HEADER],
                           req.headers[callback.SIGNATURE_HEADER])
    assert not callback.verify('other', req.content, req.headers[callback.TIMESTAMP_HEADER],
                               req.headers[callback.SIGNATURE_HEADER])
    assert json.loads(req.content)['id'] == rid
    recorded = client.get(f'/api/r/{rid}').get_json()['callback']
    assert recorded['status'] == 200 and recorded['attempts'] == 1 and not recorded['error']


def test_a_failing_receiver_is_retried_then_reported():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)
    out = callback.deliver('https://hooks.example/cc', b'{}', 's', ['hooks.example'],
                           client=httpx.Client(transport=httpx.MockTransport(handler)), pause=0)
    assert len(calls) == callback.ATTEMPTS and out['status'] == 503 and out['error'] == 'HTTP 503'


def test_signatures_expire():
    headers = callback.headers('s', b'{}', now=1_000)
    ts, sig = headers[callback.TIMESTAMP_HEADER], headers[callback.SIGNATURE_HEADER]
    assert callback.verify('s', b'{}', ts, sig, now=1_100)
    assert not callback.verify('s', b'{}', ts, sig, now=2_000)


def test_a_token_guards_every_route_but_health(tmp_path):
    client = _client(tmp_path, api_token='tok')
    assert client.get('/healthz').status_code == 200
    assert client.get('/api/check', query_string={'url': ARTICLE_URL}).status_code == 401
    assert client.post('/api/checks', json={'url': ARTICLE_URL}).status_code == 401
    home = client.get('/')
    assert home.status_code == 302 and '/login' in home.location
    ok = client.post('/api/checks', json={'url': ARTICLE_URL}, headers={'Authorization': 'Bearer tok'})
    assert ok.status_code == 202
    assert client.post('/login', data={'token': 'nope'}).status_code == 401
    signed_in = client.post('/login', data={'token': 'tok', 'next': '/'})
    assert signed_in.status_code == 303
    assert client.get('/').status_code == 200          # the session cookie now carries it
    assert client.post('/login', data={'token': 'tok', 'next': '//evil.example'}).location == '/'


def test_embed_mode_drops_the_site_chrome(tmp_path):
    client = _client(tmp_path)
    rid = client.post('/api/checks', json={'url': ARTICLE_URL}).get_json()['id']
    full = client.get(f'/r/{rid}').get_data(as_text=True)
    embed = client.get(f'/r/{rid}?embed=1').get_data(as_text=True)
    assert 'class="top"' in full and 'Check another' in full
    assert 'class="top"' not in embed and 'Check another' not in embed and 'class="embed"' in embed
    assert 'Receipt' in embed
    assert SOURCE_URL in embed
