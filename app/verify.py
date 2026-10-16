"""The receipt check: a model's verdict, held to the page it cites.

The model may only QUOTE; code holds the quote to the fetched page. That rule
is the whole product. A verdict that fails any check is not softened or retried — it
becomes `unverified`, with the failed check as its reason.
"""
import re
from datetime import date
from urllib.parse import quote as urlquote

from app import figures
from app.claims import Claim, norm

JUDGED = ('supported', 'contradicted', 'outdated')
MAX_EXCERPTS = 3        # a quote may be this many excerpts joined by "..."
MAX_EXCERPT_SPREAD = 5000  # characters on the page from the first excerpt to the last
VERDICTS = JUDGED + ('unverified', 'opinion')
MIN_QUOTE = 12


def _digits(text: str) -> str:
    return norm(text).replace(',', '')


_YEAR_RE = re.compile(r'(?<!\d)(19\d{2}|20\d{2}|2100)(?!\d)')
TIME_BOUND = ('figure', 'legal')


def _years(text: str) -> list[int]:
    return [int(y) for y in _YEAR_RE.findall(text or '')]


def stale_contradiction(claim: Claim, quote: str, article_date: str, source_date: str) -> str:
    """Why an older source cannot contradict this claim, or '' if it can.

    "Seattle's minimum wage rose to $21.30" in a 2026 article is not
    contradicted by a 2024 bulletin saying $19.97; that bulletin is simply
    older. The claim's moment is the latest year in its span, else the
    article's date. The source's moment is the latest year in its quote, else
    the page's declared date. Unknown on either side: no ruling here.
    """
    span_years = _years(claim.span)
    quote_years = _years(quote)
    if span_years:
        claim_point, claim_label = (max(span_years),), str(max(span_years))
    elif article_date:
        claim_point, claim_label = tuple(map(int, article_date.split('-'))), f'the article ({article_date})'
    else:
        return ''
    if quote_years:
        source_point, source_label = (max(quote_years),), f'the quote ({max(quote_years)})'
    elif source_date:
        source_point, source_label = tuple(map(int, source_date.split('-'))), f'the source ({source_date})'
    else:
        return ''
    n = min(len(claim_point), len(source_point))
    if source_point[:n] < claim_point[:n]:
        if span_years:
            claim_label = f'the claim ({claim_label})'
        return f'Receipt failed: {source_label} predates {claim_label}, so it cannot show the claim is wrong.'
    return ''


_WORD = re.compile(r'[^\W_]+')
_SPLIT = re.compile(r'\s*(?:\.\.\.|\u2026|\[\s*\.\.\.\s*\]|\n\s*\n)\s*')


def _words(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0).lower(), m.start(), m.end()) for m in _WORD.finditer(text)]


def find_excerpts(quote: str, page: str) -> tuple[list[str], str]:
    """The page's own text for each excerpt of `quote`, or ([], why).

    An excerpt matches when the page has the same words in the same order:
    case, whitespace and punctuation aside, word for word. Up to three
    excerpts joined by "..." (or a paragraph break), each at least 12
    characters, all within 5,000 characters of each other on the page so
    unrelated passages cannot be stitched into a receipt.
    """
    parts = [p.strip(' .,;:') for p in _SPLIT.split(quote or '')]
    parts = [p for p in parts if _WORD.search(p)]
    if not parts or len(norm(' '.join(parts))) < MIN_QUOTE:
        return [], 'no usable quote.'
    if len(parts) > MAX_EXCERPTS:
        return [], f'the quote has more than {MAX_EXCERPTS} excerpts.'
    page_words = _words(page)
    tokens = [w for w, _, _ in page_words]
    first_at: dict[str, list[int]] = {}
    for i, w in enumerate(tokens):
        first_at.setdefault(w, []).append(i)
    found = []
    for part in parts:
        want = [w for w, _, _ in _words(part)]
        if len(norm(part)) < MIN_QUOTE and len(parts) > 1:
            return [], 'an excerpt of the quote is too short to check.'
        hit = next((i for i in first_at.get(want[0], []) if tokens[i:i + len(want)] == want), None)
        if hit is None:
            return [], 'the quote is not on the cited page.'
        start, end = page_words[hit][1], page_words[hit + len(want) - 1][2]
        found.append((start, end))
    if max(e for _, e in found) - min(s for s, _ in found) > MAX_EXCERPT_SPREAD:
        return [], 'the excerpts of the quote are too far apart on the page.'
    return [' '.join(page[s:e].split()) for s, e in found], ''


def check(claim: Claim, answer: dict, pages: dict[str, str], *,
          article_date: str = '', source_dates: dict[str, str] | None = None
          ) -> tuple[str, dict | None, str]:
    """Return (verdict, receipt, reason). `pages` maps fetched URL -> full text;
    `source_dates` maps it to the page's declared date."""
    verdict = str(answer.get('verdict') or '').lower()
    reason = str(answer.get('reason') or '').strip()[:400]
    if verdict not in JUDGED:
        return 'unverified', None, reason or 'No source settled this claim.'
    url = str(answer.get('url') or '')
    quote = str(answer.get('quote') or '').strip()
    if url not in pages:
        return 'unverified', None, 'Receipt failed: the cited page was not one that was read.'
    excerpts, why = find_excerpts(quote, pages[url])
    if not excerpts:
        return 'unverified', None, f'Receipt failed: {why}'
    # From here on the receipt is the page's own words, not the model's copy.
    quote = ' … '.join(excerpts)
    if claim.kind in ('figure', 'date') and verdict == 'supported' and claim.value:
        # Compared by value ("100 million" is "100,000,000"; each date of a
        # range on its own) and as the claim states it ("exceeding 100
        # million" is met by 104 million); text only for values with no figure.
        gaps = figures.missing(claim.value, quote, claim.span)
        if gaps is None and _digits(claim.value) not in _digits(quote):
            return 'unverified', None, "Receipt failed: the quote does not contain the claim's figure."
        if gaps:
            return 'unverified', None, ("Receipt failed: the quote does not contain the claim's figure "
                                        f"({', '.join(gaps)}).")
    if verdict == 'contradicted' and claim.kind in TIME_BOUND:
        why = stale_contradiction(claim, quote, article_date, (source_dates or {}).get(url, ''))
        if why:
            return 'unverified', None, why
    as_of = str(answer.get('as_of') or '')[:10]
    if verdict == 'outdated':
        try:
            date.fromisoformat(as_of)
        except ValueError:
            return 'unverified', None, 'Receipt failed: "outdated" needs the date it changed.'
    receipt = {'url': url, 'quote': quote[:600], 'as_of': as_of, 'excerpts': excerpts,
               'link': text_fragment_url(url, *excerpts)}
    return verdict, receipt, reason


def _frag(text: str) -> str:
    # Text fragments reserve '-', ',' and '&'; quote(safe='') leaves '-' alone.
    return urlquote(text, safe='').replace('-', '%2D')


def _directive(quote: str) -> str:
    # Long quotes use the `start,end` form so the URL stays short and still
    # matches when the middle contains markup the browser renders differently.
    words = ' '.join(quote.split()).split(' ')
    if len(words) > 12:
        return f"text={_frag(' '.join(words[:5]))},{_frag(' '.join(words[-5:]))}"
    return f"text={_frag(' '.join(words))}"


def text_fragment_url(url: str, *quotes: str) -> str:
    """A link that opens `url` scrolled to and highlighting each quote
    (several excerpts become several `text=` directives)."""
    base = url.split('#', 1)[0]
    return f"{base}#:~:{'&'.join(_directive(q) for q in quotes if q.strip())}"


_PLAIN = [
    ('not one that was read', "points to a page that wasn't read"),
    ('no usable quote', 'gave no usable quote'),
    ('more than', 'stitched together more than three pieces of the page'),
    ('too far apart', 'joined passages that are far apart on the page'),
    ('too short', 'included a fragment too short to check'),
    ('not on the cited page', "doesn't appear word for word on that page"),
    ('predates', 'comes from a source older than the claim, so it cannot show it is wrong'),
    ('needs the date', "didn't say when it changed"),
]


def plain_refusal(why: str, verdict: str, url: str) -> str:
    """One sentence for a reader: what the model said, where, and why code
    did not accept it. The machine reason stays on the claim's data."""
    host = url.split('/')[2] if url.count('/') >= 2 else 'a page'
    m = re.search(r"figure \((.+)\)", why)
    if m:
        problem = f"doesn't give {m.group(1)}"
    elif "figure" in why:
        problem = "doesn't contain the claim's figure"
    else:
        problem = next((text for key, text in _PLAIN if key in why), 'could not be checked')
    return f'Not confirmed: the model called this {verdict} citing {host}, but its quote {problem}.'
