"""Claim Check: paste an article URL, get a verdict with a receipt per claim.

Routes
  GET  /                      the form and recent reports
  GET  /check?url=[&fresh=1]  start a check (or reuse a recent one) -> /r/<id>
  GET  /r/<id>[?embed=1]      the report; fills in live while it runs (embed: no site chrome)
  GET  /r/<id>/events         server-sent events for a running report
  GET  /r/<id>/body           the report body fragment
  GET  /r/<id>/claim/<n>      one claim card fragment
  GET  /r/<id>/snap/<file>    a snapshot image
  GET  /r/<id>/view.json      snapshot manifest for the in-page viewer
  GET  /r/<id>/compare/<old>  what changed since an earlier check of the same article
  GET  /r/<id>/report.pdf     the report as a PDF
  POST /r/<id>/claim/<n>/feedback   a reader's vote on a verdict (up | down)
  GET  /api/r/<id>            the report as JSON
  GET  /api/feedback          every vote with its claim, for prompt tuning
  GET  /sources               how sources are tiered
  GET  /api/check?url=        run a check synchronously, JSON (for scripts)
  POST /api/checks            start a check of a URL or a draft (html | text) -> {id}
  GET|POST /login             sign in with CC_API_TOKEN (only when it is set)
  GET  /healthz

With CC_API_TOKEN set, every route but /healthz and /login needs
`Authorization: Bearer <token>` or a browser that signed in at /login.
Plug-ins (app.plugins) may add routes and their own authentication.
"""
import hashlib
import hmac
import json
import re
from dataclasses import asdict
from pathlib import Path

from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, send_file, send_from_directory, session, url_for)

from app import callback as callback_mod
from app import plugins as plugins_mod
from app import search as search_mod
from app.compare import compare
from app.config import Config
from app.dates import page_date
from app.fetch import MAX_BYTES, Page, parse_document
from app.jobs import Jobs
from app.llm import FakeLLM, OpenAICompatLLM
from app.pipeline import Checker, clean_context
from app.search import NoSearch, OpenRouterWebSearch, SearxngSearch
from app.store import Store

SNAP_NAME = re.compile(r'^[a-z0-9-]+\.(?:png|jpg)$')
SCHEMA_VERSION = 1          # of the report JSON; raised when a field changes meaning or goes away
OPEN_ROUTES = ('healthz', 'login', 'static')


def build_checker(cfg: Config, plugins: plugins_mod.Plugins | None = None) -> Checker:
    llm = (OpenAICompatLLM(cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model)
           if cfg.llm_base_url and cfg.llm_model else FakeLLM())
    return Checker(cfg, llm, build_search(cfg), snapshotter=build_snapshotter(cfg),
                   plugins=plugins)


def draft_page(data: dict, url: str) -> Page:
    """A draft sent as `html` or `text`, read the way a fetched page is."""
    html, text = str(data.get('html') or ''), str(data.get('text') or '')
    if html:
        doc = parse_document(html)
        page = Page(url, doc.title, doc.passages, doc.links, date=page_date(html),
                    all_passages=doc.all_passages, link_context=doc.link_context,
                    back_matter_chars=doc.back_matter_chars)
    else:
        page = Page(url, '', [' '.join(p.split()) for p in re.split(r'\n\s*\n', text)], kind='text')
    page.title = str(data.get('title') or page.title)[:300]
    page.date = str(data.get('date') or page.date)[:10]
    page.passages = [p for p in page.passages if p]
    return page


def build_search(cfg: Config):
    if cfg.search == 'searxng' and cfg.searxng_url:
        return SearxngSearch(cfg.searxng_url)
    if cfg.search == 'openrouter' and cfg.openrouter_api_key:
        return OpenRouterWebSearch(cfg.openrouter_api_key, cfg.search_model or cfg.llm_model)
    return NoSearch()


def build_snapshotter(cfg: Config):
    if not cfg.snapshots:
        return None
    try:
        from app.snapshot import Snapshotter
    except ImportError:
        return None
    return Snapshotter(Path(cfg.data_dir) / 'snapshots')


def create_app(checker_factory=None, *, store: Store | None = None,
               sync_jobs: bool = False, cfg: Config | None = None,
               plugins: plugins_mod.Plugins | None = None) -> Flask:
    app = Flask(__name__)
    cfg = cfg or Config()
    if plugins is None:
        plugins = plugins_mod.load(cfg.plugins, cfg.entry_plugins) if checker_factory is None \
            else plugins_mod.Plugins()
    factory = checker_factory or (lambda: build_checker(cfg, plugins))
    store = store or Store(Path(cfg.data_dir) / 'claimcheck.db')
    store.mark_interrupted()

    def payload(rid: str) -> dict:
        r, meta = store.load(rid)
        return {**asdict(r), 'id': rid, 'created_at': meta['created_at'],
                'schema_version': SCHEMA_VERSION}

    def deliver(rid: str, url: str) -> dict:
        body = json.dumps(payload(rid)).encode()
        return callback_mod.deliver(url, body, cfg.webhook_secret, cfg.callback_hosts)

    jobs = Jobs(store, factory, sync=sync_jobs, deliver=deliver)
    snap_root = Path(cfg.data_dir).resolve() / 'snapshots'
    app.config.update(FAKE_LLM=not (cfg.llm_base_url and cfg.llm_model), STORE=store, JOBS=jobs,
                      PLUGINS=plugins)

    if cfg.api_token:
        app.secret_key = hashlib.sha256(b'claim-check session:' + cfg.api_token.encode()).digest()

        def _token_ok(given: str) -> bool:
            return hmac.compare_digest(given.encode(), cfg.api_token.encode())

        @app.before_request
        def require_token():
            if request.endpoint in OPEN_ROUTES:
                return None
            bearer = request.headers.get('Authorization', '')
            if bearer.startswith('Bearer ') and _token_ok(bearer[7:].strip()):
                return None
            if session.get('cc_auth'):
                return None
            if request.path.startswith('/api/') or request.method != 'GET':
                return jsonify({'error': 'authentication required'}), 401
            return redirect(url_for('login', next=request.full_path))

        @app.route('/login', methods=['GET', 'POST'])
        def login():
            nxt = request.values.get('next') or '/'
            if not nxt.startswith('/') or nxt.startswith('//'):
                nxt = '/'
            if request.method == 'POST':
                if _token_ok(request.form.get('token', '')):
                    session['cc_auth'] = True
                    session.permanent = True
                    return redirect(nxt, 303)
                return render_template('login.html', next=nxt, error='That token is not right.'), 401
            return render_template('login.html', next=nxt)

    plugins.init_app(app)

    def _loaded(rid: str):
        loaded = store.load(rid)
        if not loaded:
            abort(404)
        return loaded

    @app.get('/')
    def index():
        return render_template('index.html', fake=app.config['FAKE_LLM'], recent=store.recent(10), cfg=cfg)

    @app.get('/check')
    def check():
        url = (request.args.get('url') or '').strip()
        if not url:
            return render_template('index.html', fake=app.config['FAKE_LLM'], recent=store.recent(10),
                                   cfg=cfg, error='Paste an article URL to check.'), 400
        if not request.args.get('fresh') and cfg.cache_seconds > 0:
            cached = store.latest_for(url, cfg.cache_seconds)
            if cached:
                return redirect(url_for('report', rid=cached, cached=1), 303)
        return redirect(url_for('report', rid=jobs.start(url)), 303)

    def _share_image(r, rid):
        """The crop of the first decided claim, for link previews."""
        for v in ('contradicted', 'supported', 'outdated', 'unverified'):
            for c in r.claims:
                crop = (c.snapshot or {}).get('article_crop')
                if c.verdict == v and crop:
                    return url_for('snap', rid=rid, name=crop, _external=True)
        return ''

    @app.get('/r/<rid>')
    def report(rid):
        r, meta = _loaded(rid)
        return render_template('report.html', r=r, meta=meta, rid=rid,
                               cached=bool(request.args.get('cached')),
                               printing=bool(request.args.get('print')),
                               embed=bool(request.args.get('embed')),
                               previous=store.previous_for(rid) if r.status == 'done' else None,
                               feedback=store.feedback_counts(rid),
                               share_image=_share_image(r, rid))

    @app.get('/r/<rid>/body')
    def report_body(rid):
        r, meta = _loaded(rid)
        return render_template('_body.html', r=r, meta=meta, rid=rid,
                               feedback=store.feedback_counts(rid))

    @app.get('/r/<rid>/claim/<int:cid>')
    def claim_card(rid, cid):
        r, _ = _loaded(rid)
        claim = next((c for c in r.claims if c.id == cid), None)
        if not claim:
            abort(404)
        return render_template('_claim.html', c=claim, r=r, rid=rid,
                               feedback=store.feedback_counts(rid))

    @app.post('/r/<rid>/claim/<int:cid>/feedback')
    def claim_feedback(rid, cid):
        r, _ = _loaded(rid)
        if not any(c.id == cid for c in r.claims):
            abort(404)
        data = request.get_json(silent=True) or request.form
        try:
            store.add_feedback(rid, cid, str(data.get('vote', '')), str(data.get('note', '')))
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        return jsonify(store.feedback_counts(rid).get(cid, {'up': 0, 'down': 0}))

    @app.get('/r/<rid>/compare/<old>')
    def report_compare(rid, old):
        new_r, new_meta = _loaded(rid)
        old_r, old_meta = _loaded(old)
        return render_template('compare.html', new=new_r, old=old_r, rid=rid, old_id=old,
                               new_meta=new_meta, old_meta=old_meta, diff=compare(old_r, new_r))

    @app.get('/r/<rid>/report.pdf')
    def report_pdf(rid):
        r, meta = _loaded(rid)
        if r.status == 'running':
            return 'The check is still running.', 409
        from app.export import render_pdf
        path = snap_root / rid / f"report-{int(meta['updated_at'])}.pdf"
        if not path.exists():
            render_pdf(url_for('report', rid=rid, print=1, _external=True), path)
        name = re.sub(r'[^A-Za-z0-9]+', '-', r.title or 'report').strip('-')[:60] or 'report'
        return send_file(path, mimetype='application/pdf', download_name=f'claim-check-{name}.pdf')

    @app.get('/api/feedback')
    def api_feedback():
        return jsonify(store.all_feedback())

    @app.get('/sources')
    def sources_page():
        return render_template('sources.html', cfg=cfg, s=search_mod)

    @app.get('/r/<rid>/events')
    def events(rid):
        _loaded(rid)
        after = request.headers.get('Last-Event-ID') or request.args.get('after') or 0

        def stream():
            yield 'retry: 2000\n\n'
            for i, kind, data in jobs.events(rid, after=int(after)):
                if kind == 'ping':
                    yield ': ping\n\n'
                    continue
                yield f'id: {i}\nevent: {kind}\ndata: {json.dumps(data)}\n\n'

        return Response(stream(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.get('/r/<rid>/snap/<name>')
    def snap(rid, name):
        if not SNAP_NAME.match(name) or not re.fullmatch(r'[A-Za-z0-9]+', rid):
            abort(404)
        return send_from_directory(snap_root / rid, name, max_age=86400)

    @app.get('/r/<rid>/view.json')
    def view_manifest(rid):
        r, _ = _loaded(rid)
        snap = r.snapshot or {}
        claims = []
        for c in r.claims:
            cs = c.snapshot or {}
            claims.append({'id': c.id, 'verdict': c.verdict, 'span': c.span,
                           'quote': (c.receipt or {}).get('quote', ''),
                           'source': cs.get('source_view')})
        return jsonify({'base': url_for('snap', rid=rid, name='x')[:-1],
                        'article': snap.get('article'), 'claims': claims,
                        'captured_at': snap.get('captured_at', ''), 'url': r.url})

    @app.get('/api/r/<rid>')
    def api_report(rid):
        _loaded(rid)
        return jsonify(payload(rid))

    @app.get('/api/check')
    def api_check():
        url = (request.args.get('url') or '').strip()
        if not url:
            return jsonify({'error': 'url is required'}), 400
        rid = jobs.start(url)
        jobs.wait(rid)
        return jsonify(payload(rid))

    @app.post('/api/checks')
    def api_checks():
        """Start a check and return at once. JSON body:
        url, or html | text for a draft (url then optional, as its future
        address); site (the publisher, never a source); title, date (a
        draft's); context (app.pipeline.clean_context); callback_url."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'error': 'send a JSON object'}), 400
        url = str(data.get('url') or '').strip()
        draft = bool(data.get('html') or data.get('text'))
        if not (url or draft):
            return jsonify({'error': 'url, html or text is required'}), 400
        if draft and len(str(data.get('html') or data.get('text'))) > MAX_BYTES:
            return jsonify({'error': 'the draft is too large to check'}), 413
        callback_url = str(data.get('callback_url') or '').strip()
        if callback_url:
            if not cfg.webhook_secret:
                return jsonify({'error': 'callbacks need CC_WEBHOOK_SECRET on the server'}), 400
            why = callback_mod.refused(callback_url, cfg.callback_hosts)
            if why:
                return jsonify({'error': why}), 400
        context = clean_context({**(data.get('context') or {}),
                                 **({'site': data['site']} if data.get('site') else {})})
        page = html = None
        if draft:
            url = url or f"draft:{context.get('site') or 'article'}"
            page = draft_page(data, url)
            if not page.passages:
                return jsonify({'error': 'the draft has no readable text'}), 400
            html = str(data.get('html') or '')
        rid = jobs.start(url, context=context, page=page, html=html or '',
                         callback_url=callback_url)
        return jsonify({'id': rid, 'status': 'running', 'schema_version': SCHEMA_VERSION,
                        'api': url_for('api_report', rid=rid, _external=True),
                        'report': url_for('report', rid=rid, _external=True)}), 202

    @app.get('/healthz')
    def healthz():
        return {'ok': True}

    return app
