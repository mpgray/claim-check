from app.dates import iso_date, page_date


def test_meta_modified_beats_published():
    html = ('<meta property="article:published_time" content="2025-12-31T10:00:00Z">'
            '<meta property="article:modified_time" content="2026-01-02T08:00:00Z">')
    assert page_date(html) == '2026-01-02'


def test_json_ld_date_is_read():
    html = '<script type="application/ld+json">{"@graph":[{"datePublished":"2024-07-01"}]}</script>'
    assert page_date(html) == '2024-07-01'


def test_time_tag_is_the_fallback_and_nothing_is_empty():
    assert page_date('<p>Posted <time datetime="2024-03-05">March 5</time></p>') == '2024-03-05'
    assert page_date('<p>No dates here</p>') == ''


def test_invalid_dates_are_dropped():
    assert iso_date('2024-13-40') == ''


def test_related_story_dates_do_not_win():
    html = ('<script type="application/ld+json">[{"@type":"NewsArticle","datePublished":"2026-01-01"},'
            '{"@type":"ItemList","itemListElement":[{"datePublished":"2026-10-12"}]}]</script>')
    assert page_date(html) == '2026-01-01'
