"""End to end with no network: fake fetcher, fake search, FakeLLM."""
import pytest

from app import create_app
from app.config import Config
from app.fetch import FetchError, Page, parse_html
from app.llm import FakeLLM
from app.pipeline import Checker

ARTICLE_URL = 'https://blog.example/wage'
SOURCE_URL = 'https://www.dol.gov/minimum-wage'
ARTICLE_HTML = f"""<html><head><title>Wage facts</title></head><body>
<nav>Home About Contact 2026</nav>
<article>
<p>The federal minimum wage is $7.25 per hour, according to the <a href="{SOURCE_URL}">Labor Department</a>.</p>
<p>Roughly 99% of workers in this town earn more than that today, locals say.</p>
<p>Honestly this is the best town to live in, and you should move here soon.</p>
</article><footer>© 2026</footer></body></html>"""
SOURCE_HTML = """<html><body><main>
<p>The federal minimum wage for covered nonexempt employees is $7.25 per hour.</p>
</main></body></html>"""


def fake_fetch(url):
    pages = {ARTICLE_URL: ARTICLE_HTML, SOURCE_URL: SOURCE_HTML}
    if url not in pages:
        raise FetchError('HTTP 404')
    title, passages, links = parse_html(pages[url])
    return Page(url, title, passages, links)


class FakeSearch:
    def search(self, query):
        return [{'url': SOURCE_URL}, {'url': 'https://dead.example/x'}]


@pytest.fixture
def checker():
    return Checker(Config(), FakeLLM(), FakeSearch(), fetch=fake_fetch)


def test_chrome_is_not_part_of_the_article():
    title, passages, links = parse_html(ARTICLE_HTML)
    assert title == 'Wage facts'
    assert not any('Home About' in p or '©' in p for p in passages)
    assert links == [SOURCE_URL]


def test_supported_claim_carries_a_receipt_and_unsupported_does_not(checker):
    report = checker.run(ARTICLE_URL)
    by_value = {c.value: c for c in report.claims}
    wage = by_value['$7.25'] if '$7.25' in by_value else by_value['7.25']
    assert wage.verdict == 'supported' and '#:~:text=' in wage.receipt['link']
    town = next(c for c in report.claims if '99%' in c.span)
    assert town.verdict == 'unverified' and town.receipt is None
    assert report.counts['supported'] == 1
    assert report.cited_links == [SOURCE_URL]


def test_unreadable_article_is_an_error_not_an_empty_report(checker):
    report = checker.run('https://blog.example/missing')
    assert report.error and not report.claims


def _app(checker, tmp_path, **kw):
    from app.store import Store
    cfg = Config(data_dir=str(tmp_path), snapshots=False)
    return create_app(checker_factory=lambda: checker, store=Store(tmp_path / 'db.sqlite3'),
                      sync_jobs=True, cfg=cfg, **kw)


def test_routes_render(checker, tmp_path):
    client = _app(checker, tmp_path).test_client()
    assert client.get('/').status_code == 200
    assert client.get('/check').status_code == 400
    started = client.get('/check', query_string={'url': ARTICLE_URL})
    assert started.status_code == 303 and '/r/' in started.location
    page = client.get(started.location)
    assert page.status_code == 200 and b'Receipt' in page.data
    rid = started.location.rsplit('/', 1)[-1]
    data = client.get(f'/api/r/{rid}').get_json()
    assert data['counts']['supported'] == 1 and data['status'] == 'done'
    assert client.get(f'/r/{rid}/claim/1').status_code == 200
    assert b'Receipt' in client.get(f'/r/{rid}/body').data
    assert client.get('/api/check', query_string={'url': ARTICLE_URL}).get_json()['counts']['supported'] == 1


def test_recent_check_is_reused_and_fresh_forces_a_new_one(checker, tmp_path):
    client = _app(checker, tmp_path).test_client()
    first = client.get('/check', query_string={'url': ARTICLE_URL}).location
    again = client.get('/check', query_string={'url': ARTICLE_URL}).location
    assert first.split('?')[0] == again.split('?')[0] and 'cached=1' in again
    fresh = client.get('/check', query_string={'url': ARTICLE_URL, 'fresh': 1}).location
    assert fresh.split('?')[0] != first.split('?')[0]


def test_event_stream_replays_and_ends(checker, tmp_path):
    client = _app(checker, tmp_path).test_client()
    rid = client.get('/check', query_string={'url': ARTICLE_URL}).location.rsplit('/', 1)[-1]
    text = client.get(f'/r/{rid}/events').get_data(as_text=True)
    assert 'event: fetched' in text and 'event: claim' in text and text.rstrip().endswith('}')
    assert 'event: done' in text


def test_snapshot_names_are_whitelisted(checker, tmp_path):
    client = _app(checker, tmp_path).test_client()
    assert client.get('/r/abc/snap/..%2F..%2Fdb.sqlite3').status_code == 404
    assert client.get('/r/abc/snap/article.png').status_code == 404


def test_the_article_is_never_its_own_receipt():
    """Search often returns the article (or its publisher's mirror) for its own sentences."""
    class SelfSearch:
        def search(self, q):
            return [{'url': 'https://www.blog.example/wage'}, {'url': SOURCE_URL}]

    def fetch(url):
        if url == 'https://www.blog.example/wage':
            return fake_fetch(ARTICLE_URL)
        return fake_fetch(url)
    report = Checker(Config(), FakeLLM(), SelfSearch(), fetch=fetch).run(ARTICLE_URL)
    for c in report.claims:
        assert all('blog.example' not in s['url'] for s in c.sources)
        assert not c.receipt or 'blog.example' not in c.receipt['url']
    assert all(c.dropped_self == 1 for c in report.claims if c.kind != 'opinion')


def test_long_article_is_chunked_and_the_unread_part_is_counted():
    from app.pipeline import chunk_passages
    passages = ['word ' * 2000] * 30           # ~300k chars
    chunks, unread = chunk_passages(passages, size=30_000, cap=200_000)
    assert len(chunks) > 1 and all(sum(len(p) for p in c) <= 30_000 for c in chunks)
    assert unread > 90_000


def test_events_report_each_claim():
    seen = []
    report = Checker(Config(), FakeLLM(), FakeSearch(), fetch=fake_fetch).run(
        ARTICLE_URL, on_event=lambda kind, r, data: seen.append(kind))
    assert seen[0] == 'fetched' and seen[1] == 'claims' and seen[-1] == 'done'
    assert seen.count('claim') == len(report.claims) and report.status == 'done'


def test_search_outage_is_reported_not_silent():
    class Down:
        def search(self, q):
            raise RuntimeError('503')
    report = Checker(Config(), FakeLLM(), Down(), fetch=fake_fetch).run(ARTICLE_URL)
    assert 'Search failed' in report.search_error
    # The cited-link path still works.
    assert report.counts['supported'] == 1


def test_snapshots_run_after_judging_and_a_failure_never_sinks_the_report():
    seen = {}

    class Snap:
        def capture(self, report, checker):
            seen['verdicts'] = [c.verdict for c in report.claims]
            raise RuntimeError('chromium missing')
    report = Checker(Config(), FakeLLM(), FakeSearch(), fetch=fake_fetch, snapshotter=Snap()).run(ARTICLE_URL)
    assert 'pending' not in seen['verdicts']
    assert report.status == 'done' and 'chromium missing' in report.snapshot['error']


def test_default_tiers_cover_governments_universities_and_papers_of_record():
    from app.search import tier
    assert tier('https://www.seattle.gov/x', [], []) == 'official'
    assert tier('https://www.gov.uk/x', [], []) == 'official'
    assert tier('https://www.legislation.gov.uk/x', [], []) == 'official'
    assert tier('https://who.int/news', [], []) == 'official'
    assert tier('https://www.bbc.co.uk/news', [], []) == 'reference'
    assert tier('https://history.stanford.edu/a', [], []) == 'reference'
    assert tier('https://goldiesparade.co.uk/a', [], []) == 'open'
    assert tier('https://gov.example.com/a', [], []) == 'open'
    assert tier('https://www.bbc.co.uk/news', [], [], defaults=False) == 'open'


def test_sources_of_record_are_read_first():
    class Mixed:
        def search(self, q):
            return [{'url': 'https://blog.other/x'}, {'url': SOURCE_URL}]
    checker = Checker(Config(max_sources_per_claim=1), FakeLLM(), Mixed(), fetch=fake_fetch)
    report = checker.run(ARTICLE_URL)
    wage = next(c for c in report.claims if '7.25' in c.span)
    assert wage.sources[0]['url'] == SOURCE_URL and wage.sources[0]['tier'] == 'official'
