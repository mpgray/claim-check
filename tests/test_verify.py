from app.claims import Claim
from app.verify import check, text_fragment_url

PAGE = 'https://www.dol.gov/agencies/whd/minimum-wage'
PAGES = {PAGE: 'The federal minimum wage for covered nonexempt employees is $7.25 per hour.'}
CLAIM = Claim(id=1, span='The federal minimum wage is $7.25 per hour.', kind='figure', value='$7.25')


def answer(**kw):
    base = {'verdict': 'supported', 'url': PAGE,
            'quote': 'The federal minimum wage for covered nonexempt employees is $7.25 per hour.'}
    return {**base, **kw}


def test_quote_on_page_is_a_receipt():
    verdict, receipt, _ = check(CLAIM, answer(), PAGES)
    assert verdict == 'supported' and receipt['link'].startswith(PAGE + '#:~:text=')


def test_invented_quote_downgrades_to_unverified():
    """The whole product: a model that paraphrases a source gets no green tick."""
    verdict, receipt, reason = check(CLAIM, answer(quote='The minimum wage is $7.25 an hour nationally.'), PAGES)
    assert verdict == 'unverified' and receipt is None and 'not on the cited page' in reason


def test_page_that_was_not_read_is_refused():
    verdict, _, reason = check(CLAIM, answer(url='https://example.com/other'), PAGES)
    assert verdict == 'unverified' and 'not one that was read' in reason


def test_supported_figure_needs_the_figure_in_the_quote():
    pages = {PAGE: PAGES[PAGE] + ' Many states set a higher minimum wage than the federal one.'}
    verdict, _, reason = check(CLAIM, answer(quote='Many states set a higher minimum wage than the federal one.'), pages)
    assert verdict == 'unverified' and 'figure' in reason


def test_outdated_needs_a_date():
    verdict, _, _ = check(CLAIM, answer(verdict='outdated', as_of=''), PAGES)
    assert verdict == 'unverified'
    verdict, receipt, _ = check(CLAIM, answer(verdict='outdated', as_of='2026-01-01'), PAGES)
    assert verdict == 'outdated' and receipt['as_of'] == '2026-01-01'


def test_unknown_verdict_is_unverified():
    assert check(CLAIM, {'verdict': 'probably true'}, PAGES)[0] == 'unverified'


def test_text_fragment_escapes_reserved_characters():
    link = text_fragment_url('https://a.example/p#old', 'well-known, safe & sound')
    assert link == 'https://a.example/p#:~:text=well%2Dknown%2C%20safe%20%26%20sound'


def test_long_quote_uses_start_end_form():
    q = ' '.join(f'w{i}' for i in range(20))
    assert text_fragment_url('https://a.example/', q).endswith('#:~:text=w0%20w1%20w2%20w3%20w4,w15%20w16%20w17%20w18%20w19')
