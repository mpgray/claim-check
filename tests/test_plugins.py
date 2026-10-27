"""Request context and plug-ins: a deployer's own tiers, exclusions, leads and records."""
import json

import pytest

from app.config import Config
from app.fetch import Page
from app.llm import FakeLLM
from app.pipeline import Checker, clean_context
from app.plugins import Document, PluginError, Plugins, load
from tests.test_pipeline import ARTICLE_URL, SOURCE_URL, FakeSearch, fake_fetch

SISTER = 'https://sister.example/wage-guide'
SISTER_PAGE = Page(SISTER, 'Wage guide', ['The federal minimum wage is $7.25 per hour for most workers.'])
RECORD_URL = 'https://records.example/minimum-wage'


def fetch(url):
    return SISTER_PAGE if url == SISTER else fake_fetch(url)


class SisterSearch:
    def search(self, q):
        return [{'url': SISTER}, {'url': SOURCE_URL}]


def wage(report):
    return next(c for c in report.claims if '7.25' in c.span)


def test_context_keeps_only_what_the_pipeline_reads():
    ctx = clean_context({'official_domains': ['DOL.gov', 'dol.gov', ''],
                         'exclude_domains': 'a.example, https://www.b.example/x',
                         'site': 'https://www.blog.example/post', 'plugin': {'game': 'x'},
                         'other': 1})
    assert ctx == {'official_domains': ['dol.gov'], 'exclude_domains': ['a.example', 'b.example'],
                   'site': 'blog.example', 'plugin': {'game': 'x'}}
    assert clean_context('nonsense') == {}


def test_an_excluded_site_is_never_a_source_and_the_report_says_so():
    checker = Checker(Config(), FakeLLM(), SisterSearch(), fetch=fetch)
    report = checker.run(ARTICLE_URL, context={'exclude_domains': ['sister.example']})
    claim = wage(report)
    assert all('sister.example' not in s['url'] for s in claim.sources)
    assert claim.dropped_excluded == 1 and claim.verdict == 'supported'
    assert report.context['exclude_domains'] == ['sister.example']
    # Without the context the same page is read.
    again = Checker(Config(), FakeLLM(), SisterSearch(), fetch=fetch).run(ARTICLE_URL)
    assert SISTER in [s['url'] for s in wage(again).sources]


def test_context_site_counts_as_the_articles_own_site():
    report = Checker(Config(), FakeLLM(), SisterSearch(), fetch=fetch).run(
        ARTICLE_URL, context={'site': 'sister.example'})
    assert wage(report).dropped_self == 1


def test_context_tiers_apply_to_one_check_only():
    checker = Checker(Config(), FakeLLM(), SisterSearch(), fetch=fetch)
    report = checker.run(ARTICLE_URL, context={'reference_domains': ['sister.example']})
    assert next(s for s in wage(report).sources if s['url'] == SISTER)['tier'] == 'reference'
    assert checker._tier(SISTER) == 'reference'
    checker.run(ARTICLE_URL)
    assert checker._tier(SISTER) == 'open'


class Leads:
    def leads(self, claim, report):
        return [SISTER]


def test_a_plugins_leads_are_read_before_search():
    class NoResults:
        def search(self, q):
            return []
    report = Checker(Config(), FakeLLM(), NoResults(), fetch=fetch,
                     plugins=Plugins([Leads()])).run(ARTICLE_URL)
    assert SISTER in [s['url'] for s in wage(report).sources]
    assert report.plugins == ['Leads']


def test_the_best_tier_any_rule_gives_wins():
    class Promote:
        def tier(self, url):
            return 'official' if 'sister.example' in url else None
    checker = Checker(Config(), FakeLLM(), SisterSearch(), fetch=fetch, plugins=Plugins([Promote()]))
    assert checker._tier(SISTER) == 'official'
    assert checker._tier(SOURCE_URL) == 'official'
    assert checker._tier('https://elsewhere.example/') == 'open'


def test_a_failing_plugin_is_reported_and_skipped():
    class Broken:
        def leads(self, claim, report):
            raise RuntimeError('database is down')
    report = Checker(Config(), FakeLLM(), FakeSearch(), fetch=fake_fetch,
                     plugins=Plugins([Broken()])).run(ARTICLE_URL)
    assert wage(report).verdict == 'supported'
    assert report.plugin_errors == ['Broken.leads failed: database is down']


class Records:
    """A deployer's database: one record says $7.25, as the article does."""

    def __init__(self, text='Federal minimum wage: $7.25 per hour (as of 2009).', decisive=True):
        self.text, self.decisive = text, decisive
        self.calls = 0

    def documents(self, claim, report):
        self.calls += 1
        if '7.25' not in claim.span:
            return []
        return [Document(RECORD_URL, 'Minimum wage record', self.text, decisive=self.decisive)]


class Judge(FakeLLM):
    """Answers from whichever sources it is given: contradicts from a record
    that disagrees, else what FakeLLM would say. Records every call."""

    def __init__(self):
        self.seen: list[list[str]] = []

    def complete_json(self, system, user):
        if 'EXTRACT' in system or 'REWRITE' in system:
            return super().complete_json(system, user)
        sources = json.loads(user.split('SOURCES:', 1)[1])
        self.seen.append([s['url'] for s in sources])
        for s in sources:
            if s['url'] == RECORD_URL and '$9.50' in s['text']:
                return {'verdict': 'contradicted', 'url': RECORD_URL,
                        'quote': 'Federal minimum wage: $9.50 per hour', 'reason': 'The record differs.'}
        return super().complete_json(system, user)


def test_a_decisive_record_is_judged_first_and_alone():
    judge = Judge()
    report = Checker(Config(), judge, FakeSearch(), fetch=fake_fetch,
                     plugins=Plugins([Records()])).run(ARTICLE_URL)
    claim = wage(report)
    assert claim.verdict == 'supported'
    assert claim.receipt['url'] == RECORD_URL
    assert claim.receipt['kind'] == 'record' and claim.receipt['decisive'] is True
    assert [s['url'] for s in claim.sources] == [RECORD_URL]   # the web was never asked


def test_a_decisive_record_that_disagrees_is_final():
    judge = Judge()
    report = Checker(Config(), judge, FakeSearch(), fetch=fake_fetch,
                     plugins=Plugins([Records('Federal minimum wage: $9.50 per hour.')])).run(ARTICLE_URL)
    claim = wage(report)
    # The web page agrees with the article; the record does not, and the record is decisive.
    assert claim.verdict == 'contradicted' and claim.receipt['url'] == RECORD_URL


def test_a_silent_record_hands_the_claim_to_the_web():
    judge = Judge()
    records = Records('Federal overtime threshold: $684 per week.')
    report = Checker(Config(), judge, FakeSearch(), fetch=fake_fetch,
                     plugins=Plugins([records])).run(ARTICLE_URL)
    claim = wage(report)
    assert claim.verdict == 'supported' and claim.receipt['url'] == SOURCE_URL
    assert [s['url'] for s in claim.sources][:2] == [RECORD_URL, SOURCE_URL]


def test_a_records_quote_is_held_to_its_text():
    class Inventor(Judge):
        def complete_json(self, system, user):
            if 'EXTRACT' in system or 'REWRITE' in system:
                return FakeLLM.complete_json(self, system, user)
            if RECORD_URL in user:
                return {'verdict': 'contradicted', 'url': RECORD_URL,
                        'quote': 'Federal minimum wage: $12 per hour', 'reason': 'Made up.'}
            return FakeLLM.complete_json(self, system, user)
    report = Checker(Config(), Inventor(), FakeSearch(), fetch=fake_fetch,
                     plugins=Plugins([Records()])).run(ARTICLE_URL)
    claim = wage(report)
    # The invented quote is refused; the claim goes on to the web and is supported there.
    assert claim.verdict == 'supported' and claim.receipt['url'] == SOURCE_URL
    assert 'not on the cited page' in claim.rejected_receipt['why']


def test_a_record_is_not_dropped_by_site_exclusions():
    report = Checker(Config(), Judge(), FakeSearch(), fetch=fake_fetch,
                     plugins=Plugins([Records()])).run(
        ARTICLE_URL, context={'exclude_domains': ['records.example'], 'site': 'records.example'})
    assert wage(report).receipt['url'] == RECORD_URL


def test_plugins_load_from_the_setting(monkeypatch):
    loaded = load('tests.test_plugins:Leads,tests.test_plugins:Records', use_entry_points=False)
    assert loaded.names == ['Leads', 'Records'] and isinstance(loaded.items[1], Records)
    with pytest.raises(PluginError):
        load('tests.test_plugins:Missing', use_entry_points=False)
    with pytest.raises(PluginError):
        load('no-colon', use_entry_points=False)


def test_entry_point_plugins_are_found(monkeypatch):
    class EP:
        name = 'mine'

        def load(self):
            return Leads
    monkeypatch.setattr('app.plugins.entry_points', lambda group: [EP()] if group == 'claim_check.plugins' else [])
    loaded = load('')
    assert loaded.names == ['mine'] and isinstance(loaded.items[0], Leads)


def test_init_app_lets_a_plugin_add_routes(tmp_path):
    from app import create_app
    from app.store import Store

    class Routes:
        def init_app(self, app):
            app.add_url_rule('/plugin/ping', 'plugin_ping', lambda: 'pong')
    app = create_app(checker_factory=lambda: None, store=Store(tmp_path / 'db.sqlite3'),
                     cfg=Config(data_dir=str(tmp_path), snapshots=False), plugins=Plugins([Routes()]))
    assert app.test_client().get('/plugin/ping').data == b'pong'


def test_records_sharing_an_address_become_one():
    from app.pipeline import _merge_documents
    merged = _merge_documents([Document(RECORD_URL, 'A', 'Line one of the record.', decisive=True),
                               Document(RECORD_URL, 'A', 'Line two of the record.', tier='reference'),
                               Document('https://other.example/', 'B', 'Other record text.')])
    assert len(merged) == 2
    assert merged[0].text == 'Line one of the record.\nLine two of the record.'
    assert merged[0].decisive is False and merged[0].tier == 'official'
