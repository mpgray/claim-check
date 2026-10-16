from app import inflation
from app.claims import Claim
from app.config import Config
from app.fetch import Page
from app.pipeline import Checker, merge_inflation

CPI = {1996: 156.9, 2004: 188.9, 2010: 218.056, 2025: 321.943}
PRINCE = 'He played 96 concerts; the average ticket price for a show was US$61 (equivalent to US$104 in 2025).'


def test_conversion_is_found_and_the_base_year_is_inferred_from_the_text():
    conv = inflation.find(PRINCE)
    assert conv.amount == 61 and conv.adjusted == 104 and conv.target_year == 2025
    assert conv.base_year is None
    inflation.infer_base_year(conv, PRINCE, ['In April 2004, Prince released Musicology.',
                                             'That same year he toured. ' + PRINCE])
    assert conv.base_year == 2004 and conv.base_source == 'context'


def test_a_correct_conversion_checks_out_within_rounding():
    conv = inflation.find(PRINCE)
    conv.base_year, conv.base_source = 2004, 'context'
    result = inflation.check(conv, CPI)
    assert result['status'] == 'ok' and abs(result['computed'] - 103.96) < 0.01
    assert 'inferred' in result['detail']


def test_a_wrong_conversion_is_contradicted_only_with_an_explicit_base_year():
    stated = inflation.find('In 2004 tickets cost $61 (equivalent to $150 in 2025).')
    assert stated.base_year == 2004 and stated.base_source == 'sentence'
    assert inflation.check(stated, CPI)['status'] == 'wrong'
    inferred = inflation.find('Tickets cost $61 (equivalent to $150 in 2025).')
    inferred.base_year, inferred.base_source = 2004, 'context'
    assert inflation.check(inferred, CPI)['status'] == 'unsure'


def test_millions_and_inline_base_year():
    conv = inflation.find('He settled for $2.95 million in 2010 (equivalent to $4.36 million in 2025).')
    assert conv.base_year == 2010 and conv.amount == 2.95e6
    assert inflation.check(conv, CPI)['status'] == 'ok'


def test_missing_cpi_year_is_unsure_not_wrong():
    conv = inflation.find('In 2004 it cost $61 (equivalent to $110 in 2031).')
    assert inflation.check(conv, CPI)['status'] == 'unsure'


def test_merge_never_upgrades_and_wrong_contradicts_with_a_computed_receipt():
    ok = {'status': 'ok', 'detail': 'x', 'url': inflation.SOURCE_URL}
    assert merge_inflation(ok, 'unverified', 'No source.', None)[0] == 'unverified'
    wrong = {'status': 'wrong', 'detail': '$61 in 2004 is $103.96 in 2025', 'url': inflation.SOURCE_URL}
    verdict, reason, receipt = merge_inflation(wrong, 'supported', 'ok', {'quote': 'q'})
    assert verdict == 'contradicted' and receipt['kind'] == 'computed' and receipt['tier'] == 'official'


def test_bls_flat_file_parsing_keeps_annual_averages_only():
    text = ('series_id\tyear\tperiod\tvalue\tfootnote_codes\n'
            'CUUR0000SA0      \t2004\tM12\t190.3\t\n'
            'CUUR0000SA0      \t2004\tM13\t188.9\t\n'
            'CUSR0000SA0      \t2004\tM13\t999.0\t\n')
    assert inflation._parse_bls(text) == {2004: 188.9}


def test_pipeline_judges_the_original_amount_and_attaches_the_check():
    article = Page('https://w.example/prince', 'Prince', [
        'In April 2004, Prince released Musicology through a one-album agreement.', PRINCE])
    source = Page('https://src.example/tour', 'Tour', [
        'The tour had 96 concerts and the average ticket price for a show was US$61 that year.'])

    class LLM:
        def complete_json(self, system, user):
            if 'EXTRACT' in system:
                return {'claims': [{'span': PRINCE, 'kind': 'figure', 'value': 'US$104', 'query': 'q'}]}
            return {'verdict': 'supported', 'url': 'https://src.example/tour',
                    'quote': 'The tour had 96 concerts and the average ticket price for a show was US$61 that year.',
                    'reason': 'The source gives 96 concerts and $61.'}

    class Search:
        def search(self, q):
            return [{'url': 'https://src.example/tour'}]

    pages = {article.url: article, source.url: source}
    checker = Checker(Config(), LLM(), Search(), fetch=lambda u: pages[u])
    checker.cpi_loader = lambda: CPI
    claim = checker.run(article.url).claims[0]
    # The figure rule held the quote to $61 (the original), not $104.
    assert claim.verdict == 'supported' and claim.receipt
    assert claim.checks[0]['status'] == 'ok' and claim.checks[0]['base_year'] == 2004
