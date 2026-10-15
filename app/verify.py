"""The receipt check: a model's verdict, held to the page it cites.

Ported in spirit from articles-ai `supersession._verify`, where the model "may
only QUOTE" and code holds the quote to the fetched page. Here that rule is the
whole product. A verdict that fails any check is not softened or retried — it
becomes `unverified`, with the failed check as its reason.
"""
from datetime import date
from urllib.parse import quote as urlquote

from app.claims import Claim, norm

JUDGED = ('supported', 'contradicted', 'outdated')
VERDICTS = JUDGED + ('unverified', 'opinion')
MIN_QUOTE = 12


def _digits(text: str) -> str:
    return norm(text).replace(',', '')


def check(claim: Claim, answer: dict, pages: dict[str, str]) -> tuple[str, dict | None, str]:
    """Return (verdict, receipt, reason). `pages` maps fetched URL -> full text."""
    verdict = str(answer.get('verdict') or '').lower()
    reason = str(answer.get('reason') or '').strip()[:400]
    if verdict not in JUDGED:
        return 'unverified', None, reason or 'No source settled this claim.'
    url = str(answer.get('url') or '')
    quote = str(answer.get('quote') or '').strip()
    if url not in pages:
        return 'unverified', None, 'Receipt failed: the cited page was not one that was read.'
    if len(norm(quote)) < MIN_QUOTE:
        return 'unverified', None, 'Receipt failed: no usable quote.'
    if norm(quote) not in norm(pages[url]):
        return 'unverified', None, 'Receipt failed: the quote is not on the cited page.'
    if claim.kind in ('figure', 'date') and verdict == 'supported' and claim.value:
        if _digits(claim.value) not in _digits(quote):
            return 'unverified', None, "Receipt failed: the quote does not contain the claim's figure."
    as_of = str(answer.get('as_of') or '')[:10]
    if verdict == 'outdated':
        try:
            date.fromisoformat(as_of)
        except ValueError:
            return 'unverified', None, 'Receipt failed: "outdated" needs the date it changed.'
    receipt = {'url': url, 'quote': quote[:600], 'as_of': as_of,
               'link': text_fragment_url(url, quote)}
    return verdict, receipt, reason


def _frag(text: str) -> str:
    # Text fragments reserve '-', ',' and '&'; quote(safe='') leaves '-' alone.
    return urlquote(text, safe='').replace('-', '%2D')


def text_fragment_url(url: str, quote: str) -> str:
    """A link that opens `url` scrolled to and highlighting `quote`.

    Long quotes use the `start,end` form so the URL stays short and still
    matches when the middle contains markup the browser renders differently.
    """
    base = url.split('#', 1)[0]
    words = ' '.join(quote.split()).split(' ')
    if len(words) > 12:
        frag = f"{_frag(' '.join(words[:5]))},{_frag(' '.join(words[-5:]))}"
    else:
        frag = _frag(' '.join(words))
    return f'{base}#:~:text={frag}'
