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
        # A lone "m"/"b"/"k" is a scale only written against the number ("$5m").
        scale = (m['scale'] or m['short'] or '').lower()
        value *= SCALE.get(scale, 1)
        out.append(Number(value, bool(m['pct'])))
    for m in WORD_RE.finditer(text):
        value = WORDS[m['word'].lower()] * SCALE.get((m['scale'] or '').lower(), 1)
        out.append(Number(float(value)))
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


def missing(value: str, quote: str) -> list[str] | None:
    """The parts of `value` the quote does not contain, compared by value.
    [] means everything is there; None means the value has no figure or date
    this module understands (the caller falls back to comparing text)."""
    v_dates, v_numbers, v_years = parse(value)
    if not (v_dates or v_numbers or v_years):
        return None
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
        if not any(_same(n.value, q.value) and n.percent == q.percent for q in q_numbers):
            out.append(f'{n.value:,.10g}' + ('%' if n.percent else ''))
    return out
