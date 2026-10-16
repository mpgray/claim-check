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
