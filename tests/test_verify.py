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


SEATTLE = 'https://seattle.example/bulletin'
SEATTLE_TEXT = 'The 2024 minimum wage for large employers (501 or more employees) is $19.97/hour.'
SEATTLE_CLAIM = Claim(id=2, span="Seattle's minimum wage rose to $21.30 an hour", kind='figure', value='$21.30')


def test_older_source_cannot_contradict_a_current_figure():
    """A 2024 bulletin does not show a 2026 article's figure is wrong; it is just older."""
    verdict, receipt, reason = check(
        SEATTLE_CLAIM, {'verdict': 'contradicted', 'url': SEATTLE, 'quote': SEATTLE_TEXT},
        {SEATTLE: SEATTLE_TEXT}, article_date='2026-01-01')
    assert verdict == 'unverified' and receipt is None and 'predates' in reason


def test_older_page_date_counts_when_the_quote_has_no_year():
    text = 'The minimum wage for large employers is $19.97 per hour.'
    verdict, _, reason = check(
        SEATTLE_CLAIM, {'verdict': 'contradicted', 'url': SEATTLE, 'quote': text},
        {SEATTLE: text}, article_date='2026-01-01', source_dates={SEATTLE: '2024-06-01'})
    assert verdict == 'unverified' and 'source (2024-06-01)' in reason


def test_current_source_can_still_contradict():
    text = 'The 2026 minimum wage for large employers is $21.00 per hour.'
    verdict, receipt, _ = check(
        SEATTLE_CLAIM, {'verdict': 'contradicted', 'url': SEATTLE, 'quote': text},
        {SEATTLE: text}, article_date='2026-01-01')
    assert verdict == 'contradicted' and receipt


def test_claim_with_its_own_year_is_judged_by_that_year():
    claim = Claim(id=3, span='In 2009, Congress increased it to $8.25 per hour.', kind='figure', value='$8.25')
    text = 'On July 24, 2009 the federal minimum wage rose to $7.25 per hour.'
    verdict, _, _ = check(claim, {'verdict': 'contradicted', 'url': SEATTLE, 'quote': text},
                          {SEATTLE: text}, article_date='2026-01-01')
    assert verdict == 'contradicted'


from app.verify import find_excerpts

BIO = ('Prince was born Prince Rogers Nelson in Minneapolis, Minnesota on June 7, 1958. '
       'His father, John Nelson, was a songwriter. ' + 'Filler sentence about his career. ' * 20 +
       'Prince died on April 21, 2016 at the age of 57.')


def test_excerpts_joined_by_ellipsis_each_held_to_the_page():
    quote = 'Prince was born Prince Rogers Nelson in Minneapolis, Minnesota on June 7, 1958. ... Prince died on April 21, 2016 at the age of 57.'
    excerpts, why = find_excerpts(quote, BIO)
    assert len(excerpts) == 2 and why == ''
    claim = Claim(id=1, span='Prince (June 7, 1958 – April 21, 2016) was a singer.', kind='date',
                  value='June 7, 1958 – April 21, 2016')
    verdict, receipt, _ = check(claim, {'verdict': 'supported', 'url': PAGE, 'quote': quote}, {PAGE: BIO})
    assert verdict == 'supported' and receipt['link'].count('text=') == 2


def test_punctuation_drift_is_not_a_different_quote_but_a_changed_word_is():
    page = 'The singer posted a lyric video for the new song "Screwdriver," which leaked last week.'
    assert find_excerpts('posted a lyric video for the new song Screwdriver,, which leaked last week', page)[0]
    assert not find_excerpts('posted a lyric video for the new song Screwdriver, which leaked this week', page)[0]
    # The receipt is the page's own text, not the model's copy.
    assert '"Screwdriver,"' in find_excerpts('the new song Screwdriver,, which leaked', page)[0][0]


def test_excerpts_far_apart_or_too_many_are_refused():
    far = 'Born on June 7, 1958 in Minneapolis. ' + 'x ' * 4000 + 'He died on April 21, 2016 at home.'
    assert 'too far apart' in find_excerpts('Born on June 7, 1958 in Minneapolis ... He died on April 21, 2016 at home', far)[1]
    many = ' ... '.join(['Filler sentence about his career'] * 4)
    assert 'more than 3' in find_excerpts(many, BIO)[1]


def test_a_refusal_reads_as_one_plain_sentence():
    from app.verify import plain_refusal
    text = plain_refusal("Receipt failed: the quote does not contain the claim's figure (at least 100,000,000).",
                         'supported', 'https://prince.org/msg/7/123')
    assert text == ('Not confirmed: the model called this supported citing prince.org, '
                    "but its quote doesn't give at least 100,000,000.")
    assert 'more than three pieces' in plain_refusal('Receipt failed: the quote has more than 3 excerpts.',
                                                     'supported', 'https://www.wikidata.org/wiki/Q7542')
