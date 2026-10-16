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


def _ld_main(node):
    """The first JSON-LD node that carries a date: the page's own entity.
    Later nodes are often "related stories" with their own, newer dates."""
    if isinstance(node, list):
        for item in node:
            found = _ld_main(item)
            if found:
                return found
    elif isinstance(node, dict):
        if 'datePublished' in node or 'dateModified' in node:
            return node
        for key in ('@graph', 'mainEntity', 'mainEntityOfPage'):
            if key in node:
                found = _ld_main(node[key])
                if found:
                    return found
    return None


def page_date(html: str) -> str:
    """The page's own declared date, ISO, or ''.

    Page-level metadata in priority order, first hit wins; never a max over
    everything, because listing pages and rails embed other articles' dates.
    Modified beats published: the question is how current the page's figure
    is, and a page updated last week is current as of last week.
    """
    head = html            # Wikipedia's JSON-LD sits at the very end; fetch caps size
    metas: dict[str, str] = {}
    for m in _META_RE.finditer(head):
        a = _attrs(m[0])
        key = (a.get('property') or a.get('name') or a.get('itemprop') or '').lower()
        if key in _META_KEYS and key not in metas:
            d = iso_date(a.get('content', ''))
            if d:
                metas[key] = d
    for key in _META_KEYS:
        if key in metas:
            return metas[key]
    for m in _LDJSON_RE.finditer(head):
        try:
            node = _ld_main(json.loads(m[1].strip()))
        except (ValueError, TypeError):
            continue
        if node:
            d = iso_date(node.get('dateModified') or '') or iso_date(node.get('datePublished') or '')
            if d:
                return d
    m = _TIME_RE.search(head)
    return iso_date(m[1]) if m else ''
