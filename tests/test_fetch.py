import pytest

from app.fetch import FetchError, fetch_page, focused_excerpt


@pytest.mark.parametrize('url', ['http://127.0.0.1/', 'http://localhost:8001/admin',
                                 'http://169.254.169.254/latest/meta-data/',
                                 'http://10.0.0.5/', 'file:///etc/passwd'])
def test_private_and_non_http_targets_are_refused(url):
    """A public 'paste any URL' box must not reach the box it runs on."""
    with pytest.raises(FetchError):
        fetch_page(url)


def test_focused_excerpt_ranks_the_deciding_passage_first():
    passages = ['Our office hours are nine to five.', 'The minimum wage is $7.25 per hour federally.']
    assert focused_excerpt(passages, 'federal minimum wage per hour').startswith('The minimum wage')


ARTICLE = """<html><head><title>Wages</title>
<meta property="article:published_time" content="2026-01-01T09:00:00Z"></head><body>
<div class="rail"><p>Raising Successful Kids: what we lose when kids cannot connect, $500K later</p>
<a href="https://other.example/teaser">Related teaser story about a different topic entirely</a></div>
<article>
<h1>Minimum wage just went up in 19 U.S. states</h1>
<p>More than 8.3 million workers are expected to benefit from minimum wage hikes in 19 states,
according to the <a href="https://www.epi.org/blog/x">Economic Policy Institute</a>.</p>
<p>The federal minimum wage remains at $7.25 an hour, where it has stood since 2009, and
Congress has not voted to change it in the years since then despite several proposals.</p>
<p>Hawaii's minimum wage rose by $2 per hour, from $14 to $16, the largest increase of any state
this year, while several cities raised local minimums on the same day as their states.</p>
</article></body></html>"""


def test_readable_body_drops_rails_and_keeps_the_article_citation():
    from app.fetch import parse_document
    doc = parse_document(ARTICLE)
    body = ' '.join(doc.passages)
    assert '8.3 million workers' in body and 'Raising Successful Kids' not in body
    assert doc.links == ['https://www.epi.org/blog/x']
    assert doc.link_context[0][1].startswith('More than 8.3 million')
    # Every visible block is still kept for holding source quotes to the page.
    assert any('Raising Successful Kids' in p for p in doc.all_passages)


def test_pdf_text_becomes_passages():
    import io

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    from app.fetch import parse_pdf
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject(
        {NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 72 720 Td (The minimum wage in Seattle is $21.30 per hour in 2026.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    buf = io.BytesIO()
    writer.write(buf)
    title, passages = parse_pdf(buf.getvalue(), 'https://seattle.gov/notice-2026.pdf')
    assert title == 'notice-2026.pdf'
    assert any('$21.30 per hour in 2026' in p for p in passages)


def test_unreadable_pdf_is_a_fetch_error():
    with pytest.raises(FetchError):
        from app.fetch import parse_pdf
        parse_pdf(b'not a pdf', 'https://x.example/a.pdf')


def test_reference_lists_are_cut_from_the_body_but_not_from_the_top():
    from app.fetch import split_back_matter
    body = [f'Article sentence number {i} with enough words to count here.' for i in range(10)]
    refs = ['References', 'Amazon is raising its minimum wage to $15 for US workers']
    kept, back = split_back_matter(body + refs)
    assert kept == body and back == refs
    # A "Notes" box in the first 30% of the page is not the end of the article.
    kept, back = split_back_matter(['Notes'] + body)
    assert back == []


def test_wikipedia_footnotes_are_followed_to_their_citations():
    from app.fetch import parse_document
    html = """<html><body><article>
<p>He played 96 concerts; the average ticket price for a show was US$61 that year, a record for him.<sup><a href="#cite_note-12">[12]</a></sup></p>
<p>Some other paragraph that is long enough to count as part of the readable body text here.</p>
<p>Another paragraph that is also long enough to count as article text for the parser to keep.</p>
</article>
<h2>References</h2><ol>
<li id="cite_note-12"><a href="#cite_ref-12">^</a> <a class="external" href="https://www.csmonitor.com/2005/x">"Prince tops touring"</a>; see <a href="#CITEREFSmith2004">Smith 2004</a></li>
</ol>
<ul><li><cite id="CITEREFSmith2004">Smith, J. <a href="https://books.example/smith">Touring</a></cite></li></ul>
</body></html>"""
    doc = parse_document(html)
    urls = [u for u, ctx, _ in doc.link_context if ctx.startswith('He played 96 concerts')]
    assert urls == ['https://www.csmonitor.com/2005/x', 'https://books.example/smith']


def test_fragment_pages_are_told_apart_from_prose_and_tables():
    from app.fetch import MIN_PROSE_SHARE, prose_share
    wikidata = ['Prince', 'instance of', 'human', 'sex or gender', 'male', 'date of birth', '7 June 1958'] * 50
    article = ['Prince was an American singer, songwriter and musician from Minneapolis, Minnesota.'] * 10
    table = ['Hourly workers at or below the federal minimum wage by state, 2024 annual averages.',
             'Alabama', '1.0', 'Alaska', '0.4', 'Arizona', '0.6']
    assert prose_share(wikidata) < MIN_PROSE_SHARE <= prose_share(table) <= prose_share(article)


def test_env_file_wins_over_a_stale_shell(tmp_path, monkeypatch):
    from app.config import load_env_file
    env = tmp_path / '.env'
    env.write_text('# settings\nexport CC_MAX_SOURCES=5\nCC_LLM_MODEL="m/x"  \nCC_WORKERS=10   # parallel\n')
    monkeypatch.setenv('CC_MAX_SOURCES', '3')
    for key in ('CC_LLM_MODEL', 'CC_WORKERS'):
        monkeypatch.delenv(key, raising=False)
    assert load_env_file(env) == ['CC_MAX_SOURCES', 'CC_LLM_MODEL', 'CC_WORKERS']
    import os
    assert (os.environ['CC_MAX_SOURCES'], os.environ['CC_LLM_MODEL'], os.environ['CC_WORKERS']) == ('5', 'm/x', '10')
