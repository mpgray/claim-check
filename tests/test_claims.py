from app.claims import missed, validate

ARTICLE = ('The federal minimum wage is $7.25 per hour. It was last raised on July 24, 2009. '
           'Experts think it should rise.')


def test_span_must_be_verbatim_article_text():
    """A paraphrase judged as if the article said it is the failure this prevents."""
    kept, rejected = validate(ARTICLE, [
        {'span': 'The federal minimum wage is $7.25 per hour.', 'kind': 'figure', 'value': '$7.25'},
        {'span': 'The minimum wage is about seven dollars.', 'kind': 'figure'},
    ], limit=10)
    assert [c.span for c in kept] == ['The federal minimum wage is $7.25 per hour.']
    assert rejected[0]['rejected'] == 'span is not in the article'


def test_value_must_appear_in_its_span():
    """An extractor that attaches the wrong figure would get the wrong number verified."""
    kept, rejected = validate(ARTICLE, [
        {'span': 'The federal minimum wage is $7.25 per hour.', 'kind': 'figure', 'value': '8.00'},
    ], limit=10)
    assert not kept and rejected[0]['rejected'] == 'value is not in the span'


def test_whitespace_and_curly_quotes_do_not_reject_a_true_span():
    kept, _ = validate('He said “no”   twice in 2024 hearings.',
                       [{'span': 'He said "no" twice in 2024 hearings.', 'kind': 'quote'}], limit=5)
    assert len(kept) == 1


def test_limit_rejections_are_reported_not_dropped():
    kept, rejected = validate(ARTICLE, [
        {'span': 'The federal minimum wage is $7.25 per hour.', 'kind': 'figure'},
        {'span': 'It was last raised on July 24, 2009.', 'kind': 'date'},
    ], limit=1)
    assert len(kept) == 1 and rejected[0]['rejected'] == 'over the claim limit'


def test_missed_lists_figures_no_claim_covers():
    """The extractor is checked too: an uncovered figure is reported, not ignored."""
    kept, _ = validate(ARTICLE, [
        {'span': 'The federal minimum wage is $7.25 per hour.', 'kind': 'figure'}], limit=5)
    left = missed(ARTICLE, kept)
    assert any('2009' in m for m in left)
    assert not any('7.25' in m for m in left)


def test_missed_ignores_footnotes_list_numbers_and_trailing_commas():
    from app.claims import checkable_tokens
    found = checkable_tokens('In 1938, it covered 13 trades [9] and 10 million workers at 90%.')
    assert '1938' in found and '1938,' not in found
    assert '13' not in found and '9' not in found
    assert '10 million' in found and '90%' in found
