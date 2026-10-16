"""When a page says it was published or updated, read from its markup.

The date is metadata the page declares, never a model's guess. It feeds one
code rule in `verify.check`: a source older than the article cannot show that
the article's current figure is wrong.
"""
import json
import re
from datetime import date
from html import unescape

_META_KEYS = ('article:modified_time', 'article:published_time', 'og:updated_time',
              'datemodified', 'datepublished', 'date', 'pubdate', 'publishdate',
              'dc.date', 'dc.date.issued', 'dcterms.modified', 'dcterms.created',
              'sailthru.date', 'parsely-pub-date')
_META_RE = re.compile(r'<meta\b[^>]*>', re.I)
_ATTR_RE = re.compile(r'([a-zA-Z_:.-]+)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))')
_LDJSON_RE = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.I | re.S)
_TIME_RE = re.compile(r'<time\b[^>]*\bdatetime\s*=\s*["\']([^"\']+)["\']', re.I)
_ISO_RE = re.compile(r'(\d{4})-(\d{2})-(\d{2})')


def iso_date(value: str) -> str:
    """The YYYY-MM-DD at the start of a timestamp, or '' if there is none."""
    m = _ISO_RE.search(str(value or '')[:40])
    if not m:
        return ''
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return ''


def _attrs(tag: str) -> dict:
    out = {}
    for m in _ATTR_RE.finditer(tag):
        out[m[1].lower()] = unescape(m[3] if m[3] is not None else m[4] if m[4] is not None else m[5])
    return out


def _ld_dates(node, found: dict) -> None:
    if isinstance(node, list):
        for item in node:
            _ld_dates(item, found)
    elif isinstance(node, dict):
        for key in ('dateModified', 'datePublished'):
            if key in node and key not in found:
                d = iso_date(node[key])
                if d:
                    found[key] = d
        for value in node.values():
            if isinstance(value, (dict, list)):
                _ld_dates(value, found)


def page_date(html: str) -> str:
    """The page's most recent declared date (modified over published), ISO or ''.

    Modified wins because the question it answers is "how current is this
    page's figure", and a page updated last week is current as of last week.
    """
    head = html[:400_000]
    candidates: list[str] = []
    for m in _LDJSON_RE.finditer(head):
        try:
            found: dict = {}
            _ld_dates(json.loads(m[1].strip()), found)
            candidates += [found[k] for k in ('dateModified', 'datePublished') if k in found]
        except (ValueError, TypeError):
            continue
    metas: dict[str, str] = {}
    for m in _META_RE.finditer(head):
        a = _attrs(m[0])
        key = (a.get('property') or a.get('name') or a.get('itemprop') or '').lower()
        if key in _META_KEYS and key not in metas:
            d = iso_date(a.get('content', ''))
            if d:
                metas[key] = d
    candidates += [metas[k] for k in _META_KEYS if k in metas]
    if not candidates:
        m = _TIME_RE.search(head)
        if m:
            candidates.append(iso_date(m[1]))
    candidates = [c for c in candidates if c]
    return max(candidates) if candidates else ''
