"""Figures and dates compared by value, never loosened in substance."""
import pytest

from app.claims import Claim
from app.figures import missing
from app.verify import check


@pytest.mark.parametrize('value, quote', [
    ('100 million', 'He sold more than 100,000,000 records worldwide.'),
    ('$56.5 million', 'an annual income of US$56.5m, largely from touring.'),
    ('February 14, 1996', 'They married on 14 February 1996 in Minneapolis.'),
    ('February 14, 1996', 'They married on Feb. 14, 1996 in Minneapolis.'),
    ('June 7, 1958 – April 21, 2016', 'Born June 7, 1958, he died at Paisley Park on April 21, 2016.'),
    ('November 2006', 'On November 15, 2006 he was inducted into the hall.'),
    ('seven', 'He won 7 Grammy Awards over his career.'),
    ('1.0 percent', 'which is 1.0% of all hourly workers.'),
])
def test_equal_values_in_other_formats_match(value, quote):
    assert missing(value, quote) == []


@pytest.mark.parametrize('value, quote, gap', [
    ('100 million', 'He sold more than 150 million records.', '100,000,000'),
    ('June 7, 1958 – April 21, 2016', 'Prince was born on June 7, 1958.', '2016-04-21'),
    ('22', 'Thirty states and the District of Columbia.', '22'),
    ('1.0 percent', 'about 1.0 million workers', '1%'),
    ('February 14, 1996', 'They married in February 1996.', '1996-02-14'),
])
def test_different_or_missing_values_still_fail(value, quote, gap):
    assert gap in missing(value, quote)


def test_a_value_with_no_figure_falls_back_to_text():
    assert missing('a lot', 'a lot of people') is None


def test_verify_accepts_a_reformatted_figure_but_still_needs_the_quote_on_the_page():
    url = 'https://src.example/p'
    quote = 'Prince sold more than 100,000,000 records during his career.'
    claim = Claim(id=1, span='Prince sold at least 100 million records worldwide', kind='figure',
                  value='100 million')
    assert check(claim, {'verdict': 'supported', 'url': url, 'quote': quote}, {url: quote})[0] == 'supported'
    # The verbatim rule is untouched: the same quote not on the page fails.
    verdict, _, reason = check(claim, {'verdict': 'supported', 'url': url, 'quote': quote},
                               {url: 'Something else entirely is written on this page.'})
    assert verdict == 'unverified' and 'not on the cited page' in reason


def test_verify_names_the_missing_part_of_a_date_range():
    url = 'https://src.example/p'
    quote = 'Prince Rogers Nelson was born on June 7, 1958, in Minneapolis.'
    claim = Claim(id=1, span='Prince Rogers Nelson (June 7, 1958 – April 21, 2016) was a singer.',
                  kind='date', value='June 7, 1958 – April 21, 2016')
    verdict, _, reason = check(claim, {'verdict': 'supported', 'url': url, 'quote': quote}, {url: quote})
    assert verdict == 'unverified' and '2016-04-21' in reason


PRINCE = 'With estimated sales exceeding 100 million records, Prince is one of the best-selling artists.'


@pytest.mark.parametrize('value, quote, span, ok', [
    ('100 million', 'Total final estimated RECORDS sold by Prince since 1978 = 104m', PRINCE, True),
    ('100 million', 'revenue of $2 trillion', PRINCE, False),                       # absurdly far
    ('100 million', 'He sold 150 million records', 'He sold 100 million records.', False),
    ('13 million', 'Purple Rain has sold 13.4 million copies', 'It sold 13 million copies.', True),
    ('13.4 million', 'Purple Rain has sold 13 million copies', 'It sold 13.4 million copies.', False),
    ('500', 'about 520 people attended', 'Nearly 500 people attended.', True),
    ('500', 'about 600 people attended', 'Nearly 500 people attended.', False),
    ('$20', 'tickets were $15', 'Tickets cost up to $20.', True),
    ('$20', 'tickets were $25', 'Tickets cost up to $20.', False),
    ('10', 'It reached number 3 on the chart', 'It was a top 10 hit.', False),      # "top" is not "at least"
])
def test_figures_are_read_the_way_the_claim_states_them(value, quote, span, ok):
    assert (missing(value, quote, span) == []) is ok


def test_a_qualified_refusal_says_what_was_needed():
    assert missing('100 million', 'He sold 90 million records', PRINCE) == ['at least 100,000,000']
