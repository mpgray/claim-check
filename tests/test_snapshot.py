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
    view = report.snapshot['article']
    assert view['tiles'] and all((out / t['name']).exists() for t in view['tiles'])
    # Every highlight's position is recorded for the viewer's markers.
    assert set(view['rects']) == {'1', '2'}
    assert report.snapshot['article_found'] == 2
    one, two, three = report.claims
    # Inline markup, &nbsp; and a curly apostrophe do not defeat the match.
    assert one.snapshot['article_found'] and (out / one.snapshot['article_crop']).exists()
    assert two.snapshot['article_found']
    assert three.snapshot == {'article_found': False}
    assert one.snapshot['source_found'] and (out / one.snapshot['source_crop']).exists()
    src = one.snapshot['source_view']
    assert src['rect'] and all((out / t['name']).exists() for t in src['tiles'])


@needs_browser
def test_unrenderable_article_degrades_without_raising(tmp_path):
    report = Report(url='http://127.0.0.1:1/blocked', id='t2',
                    claims=[Claim(id=1, span='Anything at all here.', kind='other', verdict='unverified')])
    Snapshotter(tmp_path).capture(report)
    assert 'article_error' in report.snapshot and 'article' not in report.snapshot


@needs_browser
def test_hidden_bracket_tags_do_not_block_a_match(tmp_path):
    html = ('<p>As of July 2025<sup style="display:none">[update]</sup>, 22 states and the District '
            'of Columbia have minimum wages above the federal level.</p>')
    report = Report(url='https://w.example/a', id='t3', claims=[
        Claim(id=1, span='As of July 2025[update], 22 states and the District of Columbia have '
                         'minimum wages above the federal level.', kind='figure', verdict='supported')])
    Snapshotter(tmp_path, html_for=lambda u: html).capture(report)
    snap = report.claims[0].snapshot
    assert snap['article_found'] and snap['article_exact'] is False


@needs_browser
def test_a_lone_nbsp_node_still_separates_words(tmp_path):
    html = ('<p>an annual income of <span>$56.5</span><span>&nbsp;</span><span>million</span>, '
            'largely due to his Musicology tour.</p>')
    report = Report(url='https://w.example/b', id='t4', claims=[
        Claim(id=1, span='an annual income of $56.5 million, largely due to his Musicology tour.',
              kind='figure', verdict='supported')])
    Snapshotter(tmp_path, html_for=lambda u: html).capture(report)
    assert report.claims[0].snapshot['article_found']


@needs_browser
def test_a_tall_page_is_tiled_top_to_bottom(tmp_path):
    from app.snapshot import TILE_HEIGHT
    html = ('<div style="height:9000px">top</div><p>The last sentence is far down this very tall page.</p>')
    report = Report(url='https://w.example/c', id='t5', claims=[
        Claim(id=1, span='The last sentence is far down this very tall page.', kind='other', verdict='unverified')])
    Snapshotter(tmp_path, html_for=lambda u: html).capture(report)
    view = report.snapshot['article']
    assert len(view['tiles']) == 3 and view['tiles'][1]['top'] == TILE_HEIGHT
    assert sum(t['height'] for t in view['tiles']) == view['height']
    assert view['rects']['1']['y'] > 9000


@needs_browser
def test_text_folded_under_a_paywall_is_hidden_not_found(tmp_path):
    html = ('<p>Visible intro sentence for the page.</p>'
            '<div style="max-height:0;overflow:hidden"><p>The paywalled sentence that the fetch could read.</p></div>'
            '<div style="height:400px">Subscribe to keep reading</div>')
    report = Report(url='https://w.example/d', id='t6', claims=[
        Claim(id=1, span='Visible intro sentence for the page.', kind='other', verdict='unverified'),
        Claim(id=2, span='The paywalled sentence that the fetch could read.', kind='other', verdict='unverified')])
    Snapshotter(tmp_path, html_for=lambda u: html).capture(report)
    one, two = report.claims
    assert one.snapshot['article_found']
    assert two.snapshot['article_found'] is False and two.snapshot['article_hidden'] is True
    assert '2' not in report.snapshot['article']['rects']
