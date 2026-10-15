"""Fetch a page and turn it into passages.

Ported from articles-ai `app/ai/research.py` (`_EvidenceHTMLParser`,
`_evidence_passages`, `_focused_excerpt`). The fetch itself is new and smaller
than articles-ai's pinned transport, but keeps the part that matters for a
public URL box: every hop is resolved and refused if it lands on a private,
loopback or link-local address. A "paste any URL" form without that is an
SSRF endpoint.
"""
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 3_000_000
MAX_REDIRECTS = 5
TIMEOUT = 12.0
MAX_EXCERPT_CHARS = 6000
USER_AGENT = 'ClaimCheck/0.1 (+evidence fetch; one request per page)'


class FetchError(ValueError):
    """A page could not be read. The message is shown to the reader."""


@dataclass
class Page:
    url: str
    title: str
    passages: list[str]
    links: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return '\n\n'.join(self.passages)


class _PassageParser(HTMLParser):
    _BLOCKS = {'article', 'blockquote', 'br', 'dd', 'div', 'dt', 'h1', 'h2', 'h3',
               'h4', 'h5', 'h6', 'li', 'main', 'p', 'section', 'td', 'th', 'tr'}
    _SKIP = {'aside', 'footer', 'header', 'nav', 'noscript', 'script', 'style', 'svg',
             'form', 'button'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.passages: list[str] = []
        self.title = ''
        self.links: list[str] = []
        self._parts: list[str] = []
        self._title_parts: list[str] = []
        self._in_title = False
        self._skip_depth = 0

    def _flush(self):
        passage = ' '.join(''.join(self._parts).split())
        if passage:
            self.passages.append(passage)
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'title':
            self._in_title = True
        elif tag in self._SKIP:
            self._flush()
            self._skip_depth += 1
        elif not self._skip_depth and tag in self._BLOCKS:
            self._flush()
        if tag == 'a' and not self._skip_depth:
            href = dict(attrs).get('href')
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag == 'title':
            self.title = ' '.join(''.join(self._title_parts).split())
            self._in_title = False
        elif tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif not self._skip_depth and tag in self._BLOCKS:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)
        elif not self._skip_depth:
            self._parts.append(data)


def parse_html(html: str) -> tuple[str, list[str], list[str]]:
    """(title, passages, hrefs) — hrefs only from outside page chrome."""
    parser = _PassageParser()
    parser.feed(html)
    parser._flush()
    return parser.title, [p for p in parser.passages if p], parser.links


def article_passages(passages: list[str], min_words: int = 8) -> list[str]:
    """Drop menu crumbs and bylines; keep sentences a claim can live in."""
    return [p for p in passages if len(p.split()) >= min_words]


def _check_public(host: str) -> None:
    if not host:
        raise FetchError('That URL has no host.')
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f'Could not resolve {host}.') from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            raise FetchError(f'{host} is not a public address.')


def fetch_page(url: str, *, client: httpx.Client | None = None) -> Page:
    """GET a public http(s) page, following redirects one checked hop at a time."""
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=False,
                                    headers={'User-Agent': USER_AGENT})
    try:
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(url)
            if parts.scheme not in ('http', 'https'):
                raise FetchError('Only http and https links can be checked.')
            _check_public(parts.hostname or '')
            with client.stream('GET', url) as resp:
                if resp.is_redirect:
                    url = urljoin(url, resp.headers.get('location', ''))
                    continue
                if resp.status_code != 200:
                    raise FetchError(f'{parts.hostname} answered HTTP {resp.status_code}.')
                ctype = resp.headers.get('content-type', '').split(';')[0].strip().lower()
                if ctype not in ('text/html', 'application/xhtml+xml', 'text/plain'):
                    raise FetchError(f'Unsupported content type: {ctype or "unknown"}.')
                body = b''
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise FetchError('Page is too large to check.')
                text = body.decode(resp.encoding or 'utf-8', errors='replace')
                if ctype == 'text/plain':
                    title, links = '', []
                    passages = [' '.join(p.split()) for p in re.split(r'\n\s*\n', text)]
                else:
                    title, passages, links = parse_html(text)
                passages = [p for p in passages if p]
                if not passages:
                    raise FetchError('Page has no readable text.')
                return Page(url=str(resp.url), title=title, passages=passages, links=links)
        raise FetchError('Too many redirects.')
    except httpx.HTTPError as exc:
        raise FetchError(f'Could not fetch the page ({exc.__class__.__name__}).') from exc
    finally:
        if own:
            client.close()


_STOP = {'and', 'are', 'for', 'from', 'has', 'have', 'into', 'the', 'their',
         'this', 'that', 'with', 'was', 'were', 'will'}


def focused_excerpt(passages: list[str], focus: str, limit: int = MAX_EXCERPT_CHARS) -> str:
    """The passages most likely to decide a claim, within the model's budget."""
    terms = {w for w in re.findall(r'[a-z0-9]{3,}', focus.lower()) if w not in _STOP}
    ranked = []
    for index, passage in enumerate(passages):
        lower = passage.lower()
        score = sum(1 for t in terms if re.search(rf'\b{re.escape(t)}\b', lower))
        ranked.append((score, index, passage))
    ranked.sort(key=lambda r: (-r[0], r[1]))
    out, remaining = [], limit
    for _, _, passage in ranked:
        if remaining <= 0:
            break
        out.append(passage[:remaining])
        remaining -= len(passage) + 2
    return '\n\n'.join(out)
