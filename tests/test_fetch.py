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
