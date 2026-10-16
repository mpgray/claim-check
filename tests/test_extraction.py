"""Extraction coverage: small sections, two passes, merged without duplicates."""
from app.config import Config
from app.fetch import Page
from app.pipeline import MAX_SECTIONS, Checker, merge_passes


def test_claims_both_passes_found_come_first_and_duplicates_go():
    a = [{'span': 'Alpha is one.'}, {'span': 'Beta is two.'}, {'span': 'Alpha   is one.'}]
    b = [{'span': 'Gamma is three.'}, {'span': 'beta is two.'}]
    assert [c['span'] for c in merge_passes(a, b)] == ['Beta is two.', 'Alpha is one.', 'Gamma is three.']
    assert merge_passes(None, [{'span': ''}, 'junk']) == []


def _article(n_passages: int, words: int = 120) -> Page:
    passages = [f'Paragraph {i} states that the widget weighs {i} kilograms. ' + 'filler ' * words
                for i in range(n_passages)]
    return Page('https://a.example/x', 'Widgets', passages)


class CountingLLM:
    def __init__(self):
        self.calls = []

    def complete_json(self, system, user):
        if 'EXTRACT' not in system:
            return {'verdict': 'unverified', 'reason': 'n/a'}
        second = 'one sentence at a time' in system
        section = int(user.split('SECTION ', 1)[1].split(' ', 1)[0])
        self.calls.append((section, second))
        text = user.split('ARTICLE:', 1)[1]
        spans = [line.split('. ')[0] + '.' for line in text.strip().split('\n\n')]
        # The second pass finds one claim the first pass skipped.
        picked = spans if second else spans[:-1]
        return {'claims': [{'span': s, 'kind': 'figure'} for s in picked]}


def test_every_section_gets_two_passes_and_long_articles_cap_the_call_count():
    llm = CountingLLM()
    page = _article(40)                    # ~40 x 850 chars: about 9 sections of 4k
    report = Checker(Config(max_claims=200), llm, type('S', (), {'search': lambda s, q: []})(),
                     fetch=lambda u: page).run(page.url)
    sections = {s for s, _ in llm.calls}
    assert len(sections) > 1 and {(s, True) for s in sections} <= set(llm.calls)
    assert len(report.claims) == 40         # nothing the second pass found was lost
    long_llm = CountingLLM()
    long_page = _article(400)              # ~340k chars, capped at 200k
    Checker(Config(max_claims=5), long_llm, type('S', (), {'search': lambda s, q: []})(),
            fetch=lambda u: long_page).run(long_page.url)
    assert len({s for s, _ in long_llm.calls}) <= MAX_SECTIONS
    assert len(long_llm.calls) <= 2 * MAX_SECTIONS
