"""Subject anchoring: queries name what the article is about, and sources that
never mention it are not used."""
from app.claims import mentions, topic_key, validate_subject
from app.config import Config
from app.fetch import Page
from app.pipeline import Checker, Report

ARTICLE = Page('https://fan.example/base', 'Base Foundations — Dune Awakening Network', [
    'In Dune: Awakening, where you build matters as much as what you build.',
    'Undermining is a foundation piece losing substrate contact during a storm.'])
GAME_WIKI = Page('https://wiki.example/undermining', 'Building - Dune Awakening Wiki', [
    'Foundations in Dune Awakening can be placed on sand or rock and do not lose substrate contact.'])
OTHER_GAME = Page('https://minecraft.example/bedrock', 'Bedrock - Minecraft Wiki', [
    'Undermining a foundation piece makes it lose substrate contact in Minecraft.'])


def test_subject_must_be_in_the_article_and_too_generic_names_are_refused():
    body = ' '.join(ARTICLE.passages)
    subject, names = validate_subject(
        [{'subject': 'Dune: Awakening', 'aliases': ['Dune Awakening', 'Dune', 'Arrakis']}], ARTICLE.title, body)
    assert subject == 'Dune: Awakening'
    assert 'Dune' not in names and 'Arrakis' not in names      # too generic / not in the article
    assert validate_subject([{'subject': 'Minecraft'}], ARTICLE.title, body) == ('', [])
    assert topic_key('Dune: Awakening') == topic_key('dune awakening')


def test_mentions_is_whole_word_and_punctuation_blind():
    assert mentions('Patch notes for Dune Awakening 1.2', ['Dune: Awakening'])
    assert not mentions('The Dunes awakening festival', ['Dune: Awakening'])


def test_queries_are_anchored_to_the_subject():
    report = Report(url=ARTICLE.url, subject='Dune: Awakening', subject_names=['Dune: Awakening'])
    assert Checker.anchored('game undermining foundation', report) == 'Dune: Awakening game undermining foundation'
    assert Checker.anchored('dune awakening sandworm', report) == 'dune awakening sandworm'


def test_off_topic_sources_are_dropped_and_a_quoted_retry_fills_the_slot():
    queries = []

    class Search:
        def search(self, q):
            queries.append(q)
            if q.startswith('"'):
                return [{'url': GAME_WIKI.url}]
            return [{'url': OTHER_GAME.url}]

    class LLM:
        def complete_json(self, system, user):
            if 'EXTRACT' in system:
                return {'subject': 'Dune: Awakening', 'aliases': ['Dune Awakening'], 'claims': [{
                    'span': 'Undermining is a foundation piece losing substrate contact during a storm.',
                    'kind': 'other', 'query': 'game undermining foundation substrate'}]}
            assert 'Minecraft' not in user          # the off-topic page never reaches the judge
            return {'verdict': 'contradicted', 'url': GAME_WIKI.url,
                    'quote': 'Foundations in Dune Awakening can be placed on sand or rock and do not lose substrate contact.',
                    'reason': 'The wiki says foundations do not lose substrate contact.'}

    pages = {p.url: p for p in (ARTICLE, GAME_WIKI, OTHER_GAME)}
    report = Checker(Config(), LLM(), Search(), fetch=lambda u: pages[u]).run(ARTICLE.url)
    claim = report.claims[0]
    assert report.subject == 'Dune: Awakening'
    assert queries[0] == 'Dune: Awakening game undermining foundation substrate'
    assert queries[1].startswith('"Dune: Awakening"')
    assert claim.dropped_offtopic == 1 and [s['url'] for s in claim.sources] == [GAME_WIKI.url]
    assert claim.verdict == 'contradicted'


def test_a_disambiguated_title_subject_also_matches_its_bare_name():
    subject, names = validate_subject([{'subject': 'Prince (musician)', 'aliases': []}],
                                      'Prince (musician) - Wikipedia', 'Prince released Purple Rain in 1984.')
    assert subject == 'Prince (musician)' and 'Prince' in names
    assert mentions('Prince played 96 shows on the Musicology tour.', names)


def test_claim_names_are_found_and_plain_words_are_not():
    from app.claims import entities
    assert entities("Robin Miles narrates N. K. Jemisin's The Fifth Season well.") == \
        ['Robin Miles', 'N. K. Jemisin', 'The Fifth Season']
    assert entities('Undermining is a foundation piece losing substrate contact.') == []
    assert entities('Basic Composite | Sand | Short lifespan, fails in storms.') == ['Basic Composite']


def test_a_roundup_article_keeps_sources_that_name_the_claims_own_subject():
    article = Page('https://books.example/best', 'Narrators Who Elevate the Story', [
        'These are the best audiobook performances of the decade, chosen by our editors.',
        "Jenny Lawson's narration of her memoir turns every essay into a stand-up set."])
    review = Page('https://reviews.example/lawson', 'Jenny Lawson reads her memoir', [
        'Jenny Lawson narrates her own memoir, and the audiobook plays like a stand-up set.'])
    queries = []

    class Search:
        def search(self, q):
            queries.append(q)
            return [{'url': review.url}]

    class LLM:
        def complete_json(self, system, user):
            if 'EXTRACT' in system:
                return {'subject': 'best audiobook performances', 'claims': [{
                    'span': "Jenny Lawson's narration of her memoir turns every essay into a stand-up set.",
                    'kind': 'other', 'query': 'Jenny Lawson memoir audiobook narration'}]}
            return {'verdict': 'supported', 'url': review.url,
                    'quote': 'Jenny Lawson narrates her own memoir, and the audiobook plays like a stand-up set.',
                    'reason': 'The review says so.'}

    pages = {article.url: article, review.url: review}
    claim = Checker(Config(), LLM(), Search(), fetch=lambda u: pages[u]).run(article.url).claims[0]
    assert claim.dropped_offtopic == 0 and claim.verdict == 'supported'
    assert queries[0] == 'Jenny Lawson memoir audiobook narration'     # not blurred by the theme
