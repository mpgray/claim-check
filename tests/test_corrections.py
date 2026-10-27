"""Suggested corrections: the sentence as its verified receipt would have it,
and nothing the receipt does not say."""
from app.config import Config
from app.llm import FakeLLM
from app.pipeline import Checker
from app.verify import check_correction
from tests.test_pipeline import ARTICLE_URL, SOURCE_URL, FakeSearch, fake_fetch

SPAN = 'The federal minimum wage is $9.50 per hour, according to the Labor Department.'
QUOTE = 'The federal minimum wage for covered nonexempt employees is $7.25 per hour.'


def test_a_correction_that_takes_its_figure_from_the_receipt_is_kept():
    text = 'The federal minimum wage is $7.25 per hour, according to the Labor Department.'
    assert check_correction(SPAN, text, QUOTE) == ''


def test_a_figure_the_receipt_does_not_give_is_refused():
    text = 'The federal minimum wage is $8.00 per hour, according to the Labor Department.'
    assert check_correction(SPAN, text, QUOTE) == 'the correction adds 8, which the receipt does not say.'


def test_a_word_the_receipt_does_not_say_is_refused():
    text = 'The federal minimum wage is $7.25 per hour, according to the Treasury.'
    assert 'treasury' in check_correction(SPAN, text, QUOTE)


def test_a_date_must_come_from_the_receipt_or_its_change_date():
    span = 'The state minimum wage is $15 per hour.'
    quote = 'The state minimum wage is $16.28 per hour.'
    assert check_correction(span, 'Since 2025, the state minimum wage is $16.28 per hour.', quote) \
        == 'the correction adds 2025, which the receipt does not say.'
    assert check_correction(span, 'Since 2025, the state minimum wage is $16.28 per hour.', quote,
                            as_of='2025-01-01') == ''


def test_an_unchanged_or_sprawling_rewrite_is_refused():
    assert 'unchanged' in check_correction(SPAN, SPAN.upper(), QUOTE)
    assert 'far more' in check_correction(SPAN, SPAN + ' ' + QUOTE * 4, QUOTE * 5)


class Corrector(FakeLLM):
    """Contradicts the wage claim from the DOL page, then proposes `text`."""

    def __init__(self, text):
        self.text = text

    def complete_json(self, system, user):
        if 'EXTRACT' in system:
            return {'claims': [{'span': 'The federal minimum wage is $7.25 per hour, according to the Labor Department.',
                                'kind': 'figure', 'value': '$7.25', 'query': 'federal minimum wage'}]}
        if 'REWRITE' in system:
            return {'text': self.text}
        return {'verdict': 'contradicted', 'url': SOURCE_URL, 'reason': 'Says otherwise.',
                'quote': 'The federal minimum wage for covered nonexempt employees is $7.25 per hour.'}


def test_a_contradicted_claim_gets_a_checked_correction():
    report = Checker(Config(), Corrector('The federal minimum wage is $7.25 per hour.'),
                     FakeSearch(), fetch=fake_fetch).run(ARTICLE_URL)
    claim = report.claims[0]
    assert claim.verdict == 'contradicted'
    assert claim.correction == {'text': 'The federal minimum wage is $7.25 per hour.', 'url': SOURCE_URL}
    assert claim.rejected_correction is None


def test_an_invented_correction_is_kept_only_as_refused():
    report = Checker(Config(), Corrector('The federal minimum wage is $15 per hour.'),
                     FakeSearch(), fetch=fake_fetch).run(ARTICLE_URL)
    claim = report.claims[0]
    assert claim.correction is None
    assert claim.rejected_correction['text'] == 'The federal minimum wage is $15 per hour.'
    assert '15' in claim.rejected_correction['why']


def test_supported_claims_are_never_rewritten():
    calls = []

    class Counting(FakeLLM):
        def complete_json(self, system, user):
            calls.append('REWRITE' in system)
            return super().complete_json(system, user)
    Checker(Config(), Counting(), FakeSearch(), fetch=fake_fetch).run(ARTICLE_URL)
    assert not any(calls)
