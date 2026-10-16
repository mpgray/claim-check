"""Claims: what the model says the article asserts, held to the article.

The model proposes; this module disposes. A claim survives only if its `span`
is a verbatim piece of the article, so a paraphrase can never be judged as if
the article said it. `missed()` then checks the extractor itself: any number or
date in the article that no surviving claim covers is reported: what the
extractor drops is shown, not hidden.
"""
import re
from dataclasses import dataclass, field

KINDS = ('figure', 'date', 'legal', 'quote', 'causal', 'other', 'opinion')

# Numbers worth checking: money, percentages, figures with a unit, decimals,
# grouped thousands and years. A bare one- or two-digit integer is mostly prose
# ("13 tips", "10 states") or a list index, and `[9]` is a footnote marker.
NUMBER_RE = re.compile(
    r'(?<![\w.\[])(?:[$€£]\s?)?\d(?:[\d,]*\d)?(?:\.\d+)?'
    r'(?:\s?(?:%|percent|million|billion|trillion|bn|m|k)(?![\w]))?(?![\w\]])',
    re.I)
_MARKED = re.compile(r'[$€£%,.]|percent|illion|bn|\d\s?[mk]$', re.I)
MONTHS = ('january|february|march|april|may|june|july|august|september|october|'
          'november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec')
DATE_RE = re.compile(rf'\b(?:{MONTHS})\.?\s+\d{{1,2}}(?:,\s*\d{{4}})?\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b',
                     re.I)


def norm(text: str) -> str:
    """Whitespace- and case-insensitive, with curly quotes and dashes flattened."""
    text = str(text or '')
    text = text.translate(str.maketrans({'‘': "'", '’': "'", '“': '"',
                                         '”': '"', '–': '-', '—': '-',
                                         ' ': ' '}))
    return re.sub(r'\s+', ' ', text).strip().lower()


@dataclass
class Claim:
    id: int
    span: str
    kind: str
    value: str = ''          # the figure or date the claim turns on, if any
    query: str = ''          # a search query the extractor suggested
    verdict: str = 'pending'
    receipt: dict | None = None
    reason: str = ''
    sources: list[dict] = field(default_factory=list)
    dropped_self: int = 0    # search results that were the article's own site
    snapshot: dict | None = None
    checks: list[dict] = field(default_factory=list)   # computed by code (app.inflation)
    citations: list[dict] = field(default_factory=list)  # links in the claim's own passage, and what they said
    dropped_offtopic: int = 0  # sources read that never mention the article's subject


def validate(article_text: str, raw: list[dict], limit: int) -> tuple[list[Claim], list[dict]]:
    """Keep claims whose span is verbatim article text. Returns (kept, rejected)."""
    body = norm(article_text)
    kept, rejected, seen = [], [], set()
    for item in raw or []:
        span = str(item.get('span') or '').strip()
        kind = str(item.get('kind') or 'other').lower()
        value = str(item.get('value') or '').strip()
        why = None
        if len(norm(span)) < 12:
            why = 'span too short'
        elif norm(span) not in body:
            why = 'span is not in the article'
        elif norm(span) in seen:
            why = 'duplicate'
        elif kind not in KINDS:
            why = f'unknown kind {kind!r}'
        elif value and norm(value).replace(',', '') not in norm(span).replace(',', ''):
            why = 'value is not in the span'
        if why:
            rejected.append({**item, 'rejected': why})
            continue
        seen.add(norm(span))
        if len(kept) >= limit:
            rejected.append({**item, 'rejected': 'over the claim limit'})
            continue
        kept.append(Claim(id=len(kept) + 1, span=span, kind=kind, value=value,
                          query=str(item.get('query') or '').strip()))
    return kept, rejected


def checkable_tokens(text: str) -> list[str]:
    """Figures and dates a fact-checker would want covered."""
    found = []
    for m in DATE_RE.finditer(text):
        found.append(m.group(0))
    for m in NUMBER_RE.finditer(text):
        token = m.group(0).strip()
        digits = re.sub(r'\D', '', token)
        if _MARKED.search(token) or len(digits) >= 3:
            found.append(token)
    return found


def missed(article_text: str, claims: list[Claim]) -> list[str]:
    """Figures and dates in the article that no kept claim covers."""
    covered = ' '.join(norm(c.span) for c in claims)
    out, seen = [], set()
    for token in checkable_tokens(article_text):
        key = norm(token)
        if key and key not in covered and key not in seen:
            seen.add(key)
            out.append(token)
    return out


def topic_key(text: str) -> str:
    """`norm` with punctuation dropped: "Dune: Awakening" and "Dune Awakening"
    are the same subject."""
    return ' '.join(re.sub(r'[^\w\s]', ' ', norm(text)).split())


def _specific(key: str) -> bool:
    # A name too short or too common to tell pages apart ("Dune" would let
    # every page about the films through) is not used to filter sources.
    return len(key) >= 5 or len(key.split()) >= 2


def validate_subject(candidates: list[dict], title: str, body: str) -> tuple[str, list[str]]:
    """(subject, its names) from the extractor, kept only if the article uses
    them: the model proposes the subject, the article's own text confirms it."""
    hay = f' {topic_key(title + " " + body)} '
    for cand in candidates:
        subject = str((cand or {}).get('subject') or '').strip()
        key = topic_key(subject)
        if not key or not _specific(key) or f' {key} ' not in hay:
            continue
        names, keys = [subject], {key}
        for alias in (cand.get('aliases') or [])[:6]:
            alias = str(alias).strip()
            k = topic_key(alias)
            if k and k not in keys and _specific(k) and f' {k} ' in hay:
                names.append(alias)
                keys.add(k)
        return subject, names[:4]
    return '', []


def mentions(text: str, names: list[str], limit: int = 300_000) -> bool:
    """Does this text name any of `names` (whole words, punctuation-blind)?"""
    hay = f' {topic_key(text[:limit])} '
    return any(f' {topic_key(n)} ' in hay for n in names if topic_key(n))
