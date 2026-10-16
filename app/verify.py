"""The receipt check: a model's verdict, held to the page it cites.

Ported in spirit from articles-ai `supersession._verify`, where the model "may
only QUOTE" and code holds the quote to the fetched page. Here that rule is the
whole product. A verdict that fails any check is not softened or retried — it
becomes `unverified`, with the failed check as its reason.
"""
import re
from datetime import date
from urllib.parse import quote as urlquote

from app.claims import Claim, norm

JUDGED = ('supported', 'contradicted', 'outdated')
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
    if len(norm(quote)) < MIN_QUOTE:
        return 'unverified', None, 'Receipt failed: no usable quote.'
    if norm(quote) not in norm(pages[url]):
        return 'unverified', None, 'Receipt failed: the quote is not on the cited page.'
    if claim.kind in ('figure', 'date') and verdict == 'supported' and claim.value:
        if _digits(claim.value) not in _digits(quote):
            return 'unverified', None, "Receipt failed: the quote does not contain the claim's figure."
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
