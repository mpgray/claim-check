"""Snapshots with a real headless Chromium, on fixed HTML (no network)."""
from pathlib import Path

import pytest

from app.claims import Claim
from app.pipeline import Report
from app.snapshot import Snapshotter, allowed

ARTICLE = """<html><body><header style="position:fixed;top:0">Cookie banner</header>
<article><h1>Wages</h1>
<p>The federal minimum wage remains at <a href="#">$7.25</a> an&nbsp;hour, where it has stood since 2009.</p>
<p>Seattle’s minimum wage rose to $21.30 an hour.</p></article></body></html>"""
SOURCE = """<html><body><main><p>Intro text.</p>
<p>The federal minimum wage is $7.25 per hour effective July 24, 2009.</p></main></body></html>"""
PAGES = {'https://news.example/a': ARTICLE, 'https://www.dol.gov/x': SOURCE}


def _chromium_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.launch().close()
        return True
    except Exception:
        return False


needs_browser = pytest.mark.skipif(not _chromium_ready(), reason='playwright chromium not installed')


def test_browser_may_not_reach_private_addresses():
    assert not allowed('http://127.0.0.1/')
    assert not allowed('http://169.254.169.254/latest/meta-data/')
    assert not allowed('file:///etc/passwd')
    assert allowed('data:image/png;base64,AAAA')


@needs_browser
def test_article_and_source_are_captured_with_highlights(tmp_path):
    report = Report(url='https://news.example/a', id='t1', claims=[
        Claim(id=1, span='The federal minimum wage remains at $7.25 an hour, where it has stood since 2009.',
              kind='figure', value='$7.25', verdict='supported',
              receipt={'url': 'https://www.dol.gov/x', 'kind': 'html',
                       'quote': 'The federal minimum wage is $7.25 per hour effective July 24, 2009.'}),
        Claim(id=2, span="Seattle's minimum wage rose to $21.30 an hour.", kind='figure', verdict='unverified'),
        Claim(id=3, span='A sentence that is not on the rendered page at all.', kind='other', verdict='unverified'),
    ])
    Snapshotter(tmp_path, html_for=PAGES.__getitem__).capture(report)
    out = Path(tmp_path) / 't1'
    assert report.snapshot['article_full'] == 'article.png' and (out / 'article.png').exists()
    assert report.snapshot['article_found'] == 2
    one, two, three = report.claims
    # Inline markup, &nbsp; and a curly apostrophe do not defeat the match.
    assert one.snapshot['article_found'] and (out / one.snapshot['article_crop']).exists()
    assert two.snapshot['article_found']
    assert three.snapshot == {'article_found': False}
    assert one.snapshot['source_found'] and (out / one.snapshot['source_crop']).exists()
    assert (out / one.snapshot['source_full']).exists()


@needs_browser
def test_unrenderable_article_degrades_without_raising(tmp_path):
    report = Report(url='http://127.0.0.1:1/blocked', id='t2',
                    claims=[Claim(id=1, span='Anything at all here.', kind='other', verdict='unverified')])
    Snapshotter(tmp_path).capture(report)
    assert 'article_error' in report.snapshot and 'article_full' not in report.snapshot
