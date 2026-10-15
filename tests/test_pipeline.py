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


def test_routes_render(checker):
    app = create_app(checker_factory=lambda: checker)
    client = app.test_client()
    assert client.get('/').status_code == 200
    assert client.get('/check').status_code == 400
    page = client.get('/check', query_string={'url': ARTICLE_URL})
    assert page.status_code == 200 and b'Receipt' in page.data
    data = client.get('/api/check', query_string={'url': ARTICLE_URL}).get_json()
    assert data['counts']['supported'] == 1


def test_search_outage_is_reported_not_silent():
    class Down:
        def search(self, q):
            raise RuntimeError('503')
    report = Checker(Config(), FakeLLM(), Down(), fetch=fake_fetch).run(ARTICLE_URL)
    assert 'Search failed' in report.search_error
    # The cited-link path still works.
    assert report.counts['supported'] == 1
