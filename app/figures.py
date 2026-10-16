"""Figures and dates, compared by value.

The receipt rule says a supported figure claim's quote must contain the
claim's figure. Comparing text made that rule fail on formatting: "100
million" against "100,000,000", "February 14, 1996" against "14 February
1996", or a date range whose two dates a source gives apart. Here the claim's
value is parsed into numbers and dates, and every one of them must appear, by
value, in the quote. The quote itself is still held word for word to the page
(`verify.check`); only the comparison of figures inside it is by value.
"""
import re
from dataclasses import dataclass

MONTHS = {m: i + 1 for i, m in enumerate(
    ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
     'september', 'october', 'november', 'december'])}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS['sept'] = 9
WORDS = {w: i for i, w in enumerate(
    'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen '
    'fifteen sixteen seventeen eighteen nineteen twenty'.split())}
WORDS.update({'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60, 'seventy': 70,
              'eighty': 80, 'ninety': 90, 'hundred': 100, 'dozen': 12})
SCALE = {'thousand': 1e3, 'k': 1e3, 'million': 1e6, 'm': 1e6, 'mn': 1e6, 'billion': 1e9,
         'bn': 1e9, 'b': 1e9, 'trillion': 1e12, 'tn': 1e12}

_MON = r'(?P<{n}>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|' \
       r'sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?'
DATE_PATTERNS = [
    # June 7, 1958 / Feb. 14 1996
    re.compile(_MON.format(n='mon') + r'\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<year>\d{4})', re.I),
    # 7 June 1958 / 14th of February, 1996
    re.compile(r'(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?' + _MON.format(n='mon') + r',?\s+(?P<year>\d{4})', re.I),
    # 1996-02-14
    re.compile(r'(?P<year>\d{4})-(?P<monnum>\d{1,2})-(?P<day>\d{1,2})'),
    # November 2006
    re.compile(_MON.format(n='mon') + r',?\s+(?P<year>\d{4})', re.I),
]
YEAR_RE = re.compile(r'(?<![\d.,$])(1[5-9]\d{2}|20\d{2})(?![\d%])')
NUMBER_RE = re.compile(
    r'(?<![\w.])(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)'
    r'(?:\s*(?P<scale>thousand|million|billion|trillion|mn|bn|tn)\b|(?P<short>[kmb])\b)?'
    r'(?:\s*(?P<pct>%|percent|per\s+cent))?', re.I)
WORD_RE = re.compile(r'\b(?P<word>' + '|'.join(sorted(WORDS, key=len, reverse=True)) + r')\b'
                     r'(?:\s+(?P<scale>thousand|million|billion|trillion)\b)?', re.I)


@dataclass(frozen=True)
class Date:
    year: int
    month: int | None = None
    day: int | None = None


@dataclass(frozen=True)
class Number:
    value: float
    percent: bool = False
    step: float = 0.0     # half a unit of the last digit written: "13 million" -> 500,000


def _clean(text: str) -> str:
    text = str(text or '').replace(' ', ' ').replace(' ', ' ')
    return re.sub(r'\s+', ' ', text)


def _month(name: str) -> int | None:
    name = name.lower().rstrip('.')
    return MONTHS.get(name) or MONTHS.get(name[:3])


def dates(text: str) -> tuple[list[Date], str]:
    """Dates in `text`, most specific first, and the text with them removed
    (so their digits are not read again as plain numbers)."""
    found: list[Date] = []
    text = _clean(text)
    for pattern in DATE_PATTERNS:
        def take(m):
            g = m.groupdict()
            month = int(g['monnum']) if g.get('monnum') else _month(g.get('mon') or '')
            day = int(g['day']) if g.get('day') else None
            if month and 1 <= month <= 12 and (day is None or 1 <= day <= 31):
                found.append(Date(int(m['year']), month, day))
                return ' '
            return m.group(0)
        text = pattern.sub(take, text)
    return found, text


def numbers(text: str) -> list[Number]:
    out = []
    for m in NUMBER_RE.finditer(text):
        value = float(m['num'].replace(',', ''))
        decimals = len(m['num'].split('.', 1)[1]) if '.' in m['num'] else 0
        # A lone "m"/"b"/"k" is a scale only written against the number ("$5m").
        scale = SCALE.get((m['scale'] or m['short'] or '').lower(), 1)
        out.append(Number(value * scale, bool(m['pct']), 0.5 * 10 ** -decimals * scale))
    for m in WORD_RE.finditer(text):
        scale = SCALE.get((m['scale'] or '').lower(), 1)
        out.append(Number(float(WORDS[m['word'].lower()] * scale), False, 0.5 * scale))
    return out


def parse(text: str) -> tuple[list[Date], list[Number], list[int]]:
    """(dates, numbers, bare years) in a text."""
    found_dates, rest = dates(text)
    years = [int(y) for y in YEAR_RE.findall(rest)]
    for y in years:
        rest = re.sub(rf'(?<![\d.,$]){y}(?![\d%])', ' ', rest, count=1)
    return found_dates, numbers(rest), years


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


# How the claim states its figure, read from the words just before it.
_MIN = (r'more than|over|exceed(?:s|ed|ing)?|at least|upwards of|in excess of|north of|'
        r'no fewer than|no less than|surpass(?:es|ed|ing)?')
_MAX = r'less than|fewer than|under|below|at most|up to|no more than|as many as|as much as'
_APPROX = (r'about|around|roughly|approximately|nearly|almost|some|close to|circa|'
           r'an estimated|estimated|more or less|~')
_QUALIFIER = re.compile(rf'(?:(?P<min>{_MIN})|(?P<max>{_MAX})|(?P<approx>{_APPROX}))'
                        r'\s*(?:(?:an?|the)\s+)?(?:us\s*)?\$?\s*$', re.I)
_TOLERANCE = 0.10        # "about", "nearly": within 10%
_RANGE = 10              # "more than 100 million" is not met by "$2 trillion"


_LEAD = re.compile(rf'^\s*(?:(?P<min>{_MIN})|(?P<max>{_MAX})|(?P<approx>{_APPROX}))\b', re.I)


def _kind(m) -> str:
    return next(k for k in ('min', 'max', 'approx') if m.group(k))


def qualifier(value: str, span: str) -> str:
    """'min', 'max', 'approx' or '' for how `span` states `value`: the
    qualifier closest before the figure wins ("estimated sales exceeding 100
    million" is 'min')."""
    v = _clean(value).lower().strip()
    lead = _LEAD.match(v)
    if lead:
        return _kind(lead)
    flat = _clean(span).lower()
    at = flat.find(v) if v else -1
    if at > 0:
        m = _QUALIFIER.search(flat[max(0, at - 40):at])
        if m:
            return _kind(m)
    return ''


def _satisfies(n: Number, q: Number, how: str) -> bool:
    if n.percent != q.percent:
        return False
    v, x = n.value, q.value
    if how == 'min':
        return v <= x <= v * _RANGE if v > 0 else x >= v
    if how == 'max':
        return v / _RANGE <= x <= v if v > 0 else x <= v
    if how == 'approx':
        return abs(x - v) <= _TOLERANCE * abs(v)
    # As written: equal to the claim's own rounding ("13 million" is met by 13.4 million).
    return abs(x - v) <= max(n.step, 1e-9 * max(1.0, abs(v)))


def _label(n: Number, how: str) -> str:
    text = f'{n.value:,.10g}' + ('%' if n.percent else '')
    return {'min': f'at least {text}', 'max': f'at most {text}', 'approx': f'about {text}'}.get(how, text)


def missing(value: str, quote: str, span: str = '') -> list[str] | None:
    """The parts of `value` the quote does not support, compared by value.

    Dates and years must match. A number is read the way the claim states it
    (`span`): "more than / at least X" needs a source figure at or above X,
    "under / up to X" one at or below, "about / nearly X" one within 10%, and
    a plain X one equal to X at the precision X was written. [] means
    supported; None means the value has no figure or date this module reads
    (the caller falls back to comparing text).
    """
    v_dates, v_numbers, v_years = parse(value)
    if not (v_dates or v_numbers or v_years):
        return None
    how = qualifier(value, span or value)
    q_dates, q_numbers, q_years = parse(quote)
    q_all_years = set(q_years) | {d.year for d in q_dates} | \
        {int(n.value) for n in q_numbers if n.value.is_integer() and 1500 <= n.value <= 2100}
    out = []
    for d in v_dates:
        ok = any(q.year == d.year and q.month == d.month and (d.day is None or q.day == d.day)
                 for q in q_dates)
        if not ok:
            out.append(f'{d.year}-{d.month:02d}' + (f'-{d.day:02d}' if d.day else ''))
    for y in v_years:
        if y not in q_all_years:
            out.append(str(y))
    for n in v_numbers:
        if not any(_satisfies(n, q, how) for q in q_numbers):
            out.append(_label(n, how))
    return out
