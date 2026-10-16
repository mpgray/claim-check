"""Inflation conversions, computed by code from official CPI data.

"US$61 (equivalent to US$104 in 2025)" is not a fact any source restates; it
is arithmetic on a price index. So code does the arithmetic: the amount times
CPI-U(target year) / CPI-U(base year), from the Bureau of Labor Statistics
annual averages (series CUUR0000SA0, the index Wikipedia's inflation template
uses). The model is not asked.

The base year comes from the claim itself when it says ("$61 in 2004 (...)",
or a year earlier in the sentence), else the nearest earlier year in the
surrounding article text, and the report says which. Only an explicit base
year can turn a mismatch into "contradicted": an inferred year that does not
fit means "could not confirm", never "wrong".
"""
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.fetch import USER_AGENT  # BLS refuses some agent strings; this one is accepted

BLS_FLAT_FILE = 'https://download.bls.gov/pub/time.series/cu/cu.data.1.AllItems'
FRED_CSV = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCNS'
SERIES = 'CUUR0000SA0'
SOURCE_URL = 'https://data.bls.gov/timeseries/CUUR0000SA0'
CACHE_DAYS = 7
MATCH_TOLERANCE = 0.02     # beyond rounding of the stated figure
WRONG_TOLERANCE = 0.10     # off by more than this, with an explicit base year: contradicted

_NUM = r'\d[\d,]*(?:\.\d+)?'
_UNIT = r'(?:\s*(?P<{0}>million|billion|trillion|bn|m)\b)?'
CONVERSION_RE = re.compile(
    rf'(?P<cur>US\$|\$)\s?(?P<amt>{_NUM}){_UNIT.format("unit")}'
    r'(?:\s+in\s+(?P<base>(?:17|18|19|20)\d{2}))?'
    r'\s*[\(\[,]\s*(?:equivalent\s+to|about|roughly|approximately|around|or|some|nearly)\s+'
    rf'(?P<cur2>US\$|\$)\s?(?P<amt2>{_NUM}){_UNIT.format("unit2")}'
    r'\s+in\s+(?P<year>(?:19|20)\d{2})(?:\s+dollars)?\s*[\)\]]?',
    re.I)
_YEAR = re.compile(r'(?<!\d)(1[789]\d{2}|20\d{2})(?!\d)')
_SCALE = {'': 1, 'm': 1e6, 'million': 1e6, 'bn': 1e9, 'billion': 1e9, 'trillion': 1e12}


@dataclass
class Conversion:
    text: str            # the matched words, verbatim from the span
    amount: float
    amount_text: str
    adjusted: float
    adjusted_text: str
    target_year: int
    base_year: int | None = None
    base_source: str = ''   # 'stated' | 'sentence' | 'context'


def _number(text: str, unit: str | None) -> float:
    return float(text.replace(',', '')) * _SCALE[(unit or '').lower()]


def find(span: str) -> Conversion | None:
    m = CONVERSION_RE.search(span)
    if not m:
        return None
    conv = Conversion(
        text=m.group(0).strip(), amount=_number(m['amt'], m['unit']),
        amount_text=f"{m['cur']}{m['amt']}" + (f" {m['unit']}" if m['unit'] else ''),
        adjusted=_number(m['amt2'], m['unit2']),
        adjusted_text=f"{m['cur2']}{m['amt2']}" + (f" {m['unit2']}" if m['unit2'] else ''),
        target_year=int(m['year']))
    if m['base']:
        conv.base_year, conv.base_source = int(m['base']), 'stated'
    else:
        # A year earlier in the same sentence ("In 2004 he charged $61 (...)").
        before = [int(y) for y in _YEAR.findall(span[:m.start()]) if int(y) < conv.target_year]
        if before:
            conv.base_year, conv.base_source = before[-1], 'sentence'
    return conv


def infer_base_year(conv: Conversion, span: str, passages: list[str]) -> None:
    """The nearest year before the claim in its passage or the two before it."""
    if conv.base_year:
        return
    key = ' '.join(span.split()).lower()[:80]
    for i, passage in enumerate(passages):
        flat = ' '.join(passage.split())
        at = flat.lower().find(key)
        if at < 0:
            continue
        text = ' '.join(' '.join(p.split()) for p in passages[max(0, i - 2):i]) + ' ' + flat[:at]
        years = [int(y) for y in _YEAR.findall(text) if int(y) < conv.target_year]
        if years:
            conv.base_year, conv.base_source = years[-1], 'context'
        return


def _significance(text: str) -> float:
    """Half a unit of the last digit the article wrote: '$104' -> 0.5,
    '$1.2 billion' -> 0.05 billion."""
    m = re.search(r'(\d[\d,]*)(?:\.(\d+))?', text)
    places = len(m.group(2)) if m and m.group(2) else 0
    unit = re.search(r'(million|billion|trillion|bn|m)\b', text, re.I)
    return 0.5 * 10 ** -places * _SCALE[(unit.group(1).lower() if unit else '')]


def _money(value: float) -> str:
    for unit, size in (('trillion', 1e12), ('billion', 1e9), ('million', 1e6)):
        if value >= size:
            return f'${value / size:,.2f} {unit}'
    return f'${value:,.2f}'


def check(conv: Conversion, cpi: dict[int, float]) -> dict:
    """{status: ok | wrong | unsure, detail, ...}. Pure arithmetic on `cpi`."""
    out = {'kind': 'inflation', 'source': 'BLS CPI-U annual average (CUUR0000SA0)',
           'url': SOURCE_URL, 'stated': conv.adjusted_text, 'amount': conv.amount_text,
           'target_year': conv.target_year, 'base_year': conv.base_year,
           'base_source': conv.base_source, 'text': conv.text}
    if not conv.base_year:
        return {**out, 'status': 'unsure',
                'detail': 'The year the original amount is from is not stated near the claim.'}
    if conv.base_year not in cpi or conv.target_year not in cpi:
        missing = conv.target_year if conv.target_year not in cpi else conv.base_year
        return {**out, 'status': 'unsure',
                'detail': f'BLS has no annual CPI-U average for {missing} yet.'}
    computed = conv.amount * cpi[conv.target_year] / cpi[conv.base_year]
    allowed = max(MATCH_TOLERANCE * conv.adjusted, _significance(conv.adjusted_text))
    off = abs(computed - conv.adjusted) / conv.adjusted if conv.adjusted else 1
    how = {'stated': 'as the claim states', 'sentence': 'from the same sentence',
           'context': 'inferred from the text before the claim'}[conv.base_source]
    detail = (f'{conv.amount_text} in {conv.base_year} is {_money(computed)} in {conv.target_year} '
              f'(CPI-U {cpi[conv.base_year]:g} → {cpi[conv.target_year]:g}); the article says '
              f'{conv.adjusted_text}. Base year {conv.base_year}, {how}.')
    out.update(computed=round(computed, 2), cpi_base=cpi[conv.base_year],
               cpi_target=cpi[conv.target_year], detail=detail)
    if abs(computed - conv.adjusted) <= allowed:
        return {**out, 'status': 'ok'}
    if off > WRONG_TOLERANCE and conv.base_source in ('stated', 'sentence'):
        return {**out, 'status': 'wrong'}
    return {**out, 'status': 'unsure'}


# --- CPI data ---------------------------------------------------------------

_lock = threading.Lock()
_memo: dict[str, tuple[float, dict[int, float]]] = {}


def _parse_bls(text: str) -> dict[int, float]:
    out = {}
    for line in text.splitlines():
        parts = line.split('\t')
        if len(parts) >= 4 and parts[0].strip() == SERIES and parts[2].strip() == 'M13':
            out[int(parts[1])] = float(parts[3])
    return out


def _parse_fred(text: str) -> dict[int, float]:
    months: dict[int, list[float]] = {}
    for line in text.splitlines()[1:]:
        day, _, value = line.partition(',')
        try:
            months.setdefault(int(day[:4]), []).append(float(value))
        except ValueError:
            continue
    return {y: round(sum(v) / 12, 3) for y, v in months.items() if len(v) == 12}


def load_cpi(cache_dir: str | Path, fetch=None) -> dict[int, float]:
    """Annual CPI-U averages, from a 7-day disk cache, else BLS, else FRED."""
    path = Path(cache_dir) / 'cpi_us.json'
    with _lock:
        hit = _memo.get(str(path))
        if hit and time.time() - hit[0] < CACHE_DAYS * 86400:
            return hit[1]
        if path.exists() and time.time() - path.stat().st_mtime < CACHE_DAYS * 86400:
            data = {int(k): v for k, v in json.loads(path.read_text()).items()}
            _memo[str(path)] = (time.time(), data)
            return data
        get = fetch or (lambda url: httpx.get(url, timeout=30, headers={'User-Agent': USER_AGENT},
                                              follow_redirects=True).raise_for_status().text)
        data: dict[int, float] = {}
        for url, parse in ((BLS_FLAT_FILE, _parse_bls), (FRED_CSV, _parse_fred)):
            try:
                data = parse(get(url))
            except Exception:
                data = {}
            if len(data) > 50:
                break
        if not data:
            raise RuntimeError('CPI data could not be downloaded from BLS or FRED.')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        _memo[str(path)] = (time.time(), data)
        return data
