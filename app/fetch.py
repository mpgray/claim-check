"""Fetch a page and turn it into passages.

Passages, then focused excerpts for the model. The fetch keeps the part that
matters for a public URL box: every hop is resolved and refused if it lands on a private,
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

from app.dates import page_date

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
    passages: list[str]          # readable body (the article, not its chrome)
    links: list[str] = field(default_factory=list)
    date: str = ''               # declared in the page's markup (app.dates), or ''
    kind: str = 'html'           # html | text | pdf
    all_passages: list[str] = field(default_factory=list)   # every visible block
    link_context: list[tuple[str, str, int]] = field(default_factory=list)  # (href, its passage, offset in it)
    back_matter_chars: int = 0   # reference lists, "see also" etc. not part of the body

    @property
    def text(self) -> str:
        """What a receipt quote is held to: every visible block, not only the
        readable body, so a figure in a table or sidebar still counts."""
        return '\n\n'.join(self.all_passages or self.passages)

    @property
    def source_passages(self) -> list[str]:
        return self.all_passages or self.passages


class _PassageParser(HTMLParser):
    _BLOCKS = {'article', 'blockquote', 'br', 'dd', 'div', 'dt', 'h1', 'h2', 'h3',
               'h4', 'h5', 'h6', 'li', 'main', 'p', 'section', 'td', 'th', 'tr'}
    _SKIP = {'aside', 'footer', 'header', 'nav', 'noscript', 'script', 'style', 'svg',
             'form', 'button', 'template'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.passages: list[str] = []
        self.title = ''
        self.links: list[tuple[str, str, int]] = []
        # Links inside elements that have an id, so an in-page footnote link
        # ("#cite_note-12") can be followed to the citations it points at.
        self.id_links: dict[str, list[str]] = {}
        self._ids: list[list] = []      # [id, tag, depth of that tag]
        self._pending: list[tuple[str, int]] = []
        self._parts: list[str] = []
        self._title_parts: list[str] = []
        self._in_title = False
        self._skip_depth = 0

    def _flush(self):
        passage = ' '.join(''.join(self._parts).split())
        if passage:
            self.passages.append(passage)
        # A link belongs to the passage it sits in; that is how a citation is
        # tied to the sentence it supports, and how chrome links are told apart.
        self.links += [(href, passage, offset) for href, offset in self._pending]
        self._pending, self._parts = [], []

    _VOID = {'br', 'img', 'hr', 'input', 'meta', 'link', 'wbr', 'source', 'area', 'col'}

    def _track_ids(self, tag, attrs):
        for entry in self._ids:
            if entry[1] == tag:
                entry[2] += 1
        ident = dict(attrs).get('id')
        if ident and tag not in self._VOID:
            self._ids.append([ident, tag, 1])
        href = dict(attrs).get('href') if tag == 'a' else None
        if href:
            for entry in self._ids:
                self.id_links.setdefault(entry[0], []).append(href)

    def _untrack_ids(self, tag):
        for entry in self._ids:
            if entry[1] == tag:
                entry[2] -= 1
        self._ids = [e for e in self._ids if e[2] > 0]

    def handle_starttag(self, tag, attrs):
        self._track_ids(tag, attrs)
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
                # Where in the passage the link sits: a footnote right after a
                # sentence cites that sentence, not the whole paragraph.
                self._pending.append((href, len(' '.join(''.join(self._parts).split()))))

    def handle_endtag(self, tag):
        self._untrack_ids(tag)
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


MIN_READABLE_CHARS = 200


def _norm(text: str) -> str:
    return ' '.join(str(text).split()).lower()


def follow_footnote(ident: str, id_links: dict[str, list[str]], hops: int = 2) -> list[str]:
    """External links a footnote points at, following in-page links up to
    `hops` deep (Wikipedia: [12] -> the note -> the "Smith 2004" source)."""
    out: list[str] = []
    for href in id_links.get(ident, []):
        if href.startswith('#'):
            if hops > 1:
                out += follow_footnote(href[1:], id_links, hops - 1)
        elif href.startswith(('http://', 'https://', '//')):
            out.append('https:' + href if href.startswith('//') else href)
    seen: set = set()
    return [h for h in out if not (h in seen or seen.add(h))]


def readable_passages(html: str) -> list[str]:
    """The article body by trafilatura's readability rules, or [] if it found
    too little to trust (then the caller falls back to every block)."""
    try:
        import trafilatura
        text = trafilatura.extract(html, include_tables=True, include_comments=False,
                                   include_links=False, favor_recall=True,
                                   output_format='txt') or ''
    except Exception:
        return []
    passages = [' '.join(line.split()) for line in text.splitlines()]
    passages = [p for p in passages if p]
    return passages if sum(len(p) for p in passages) >= MIN_READABLE_CHARS else []


BACK_MATTER = {'references', 'notes', 'see also', 'further reading', 'external links',
               'sources', 'citations', 'bibliography', 'footnotes', 'notes and references',
               'works cited', 'related articles', 'related stories', 'read more', 'more from'}


def split_back_matter(passages: list[str], min_share: float = 0.3) -> tuple[list[str], list[str]]:
    """(body, back matter): cut at the first "References"/"See also"-style
    heading past the first 30% of the page. A reference list is citation
    titles, not the article's claims."""
    start = int(len(passages) * min_share)
    for i in range(max(start, 1), len(passages)):
        text = passages[i].strip().strip(':').strip().lower()
        if len(text) <= 40 and text in BACK_MATTER:
            return passages[:i], passages[i:]
    return passages, []


@dataclass
class Parsed:
    title: str
    passages: list[str]
    all_passages: list[str]
    links: list[str]                       # hrefs inside the readable body
    link_context: list[tuple[str, str, int]]
    back_matter_chars: int = 0             # reference lists etc. cut from the body


def parse_document(html: str) -> Parsed:
    parser = _PassageParser()
    parser.feed(html)
    parser._flush()
    every = [p for p in parser.passages if p]
    readable, back = split_back_matter(readable_passages(html) or every)
    body = _norm(' '.join(readable))
    # Keep a link only if the passage it sits in is part of the readable body:
    # this is what drops "related stories" rails and footer link farms.
    context = []
    for href, passage, offset in parser.links:
        if not passage or _norm(passage)[:160] not in body:
            continue
        if href.startswith('#'):
            # A footnote marker: the citation is in the reference list.
            context += [(h, passage, offset) for h in follow_footnote(href[1:], parser.id_links)]
        else:
            context.append((href, passage, offset))
    return Parsed(parser.title, readable, every, [h for h, _, _ in context], context,
                  sum(len(p) for p in back))


def parse_html(html: str) -> tuple[str, list[str], list[str]]:
    """(title, readable passages, hrefs in the readable body)."""
    doc = parse_document(html)
    return doc.title, doc.passages, doc.links


MAX_PDF_BYTES = 12_000_000
MAX_PDF_PAGES = 80


def parse_pdf(data: bytes, url: str) -> tuple[str, list[str]]:
    """(title, passages) from a PDF's text layer. A scanned PDF has none: that
    is an error to show, not a page of nothing."""
    import io

    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        title = str((reader.metadata or {}).get('/Title') or '').strip()
        passages: list[str] = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            text = page.extract_text() or ''
            text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)      # re-join hyphenated words
            for block in re.split(r'\n\s*\n', text):
                lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
                # Group lines into paragraph-sized passages so ranking works.
                buf = ''
                for line in lines:
                    buf = f'{buf} {line}'.strip()
                    if len(buf) >= 500 and buf.endswith(('.', ':', ';')):
                        passages.append(buf)
                        buf = ''
                if buf:
                    passages.append(buf)
    except Exception as exc:
        raise FetchError(f'Could not read the PDF ({exc.__class__.__name__}).') from exc
    if not title:
        title = urlsplit(url).path.rsplit('/', 1)[-1] or url
    return title, passages


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
                is_pdf = ctype == 'application/pdf' or (
                    ctype in ('application/octet-stream', 'binary/octet-stream')
                    and parts.path.lower().endswith('.pdf'))
                if not is_pdf and ctype not in ('text/html', 'application/xhtml+xml', 'text/plain'):
                    raise FetchError(f'Unsupported content type: {ctype or "unknown"}.')
                limit = MAX_PDF_BYTES if is_pdf else MAX_BYTES
                body = b''
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > limit:
                        raise FetchError('Page is too large to check.')
                page = Page(url=str(resp.url), title='', passages=[])
                if is_pdf:
                    page.title, page.passages = parse_pdf(body, page.url)
                    page.kind = 'pdf'
                else:
                    text = body.decode(resp.encoding or 'utf-8', errors='replace')
                    if ctype == 'text/plain':
                        page.passages = [' '.join(p.split()) for p in re.split(r'\n\s*\n', text)]
                        page.kind = 'text'
                    else:
                        doc = parse_document(text)
                        page.title, page.passages, page.all_passages = doc.title, doc.passages, doc.all_passages
                        page.links, page.link_context = doc.links, doc.link_context
                        page.back_matter_chars = doc.back_matter_chars
                        page.date = page_date(text)
                page.passages = [p for p in page.passages if p]
                if not page.passages:
                    raise FetchError('Page has no readable text'
                                     + (' (a scanned PDF?).' if is_pdf else '.'))
                return page
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
