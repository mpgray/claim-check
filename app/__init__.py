"""Claim Check: paste an article URL, get a verdict with a receipt per claim.

Routes
  GET  /                      the form and recent reports
  GET  /check?url=[&fresh=1]  start a check (or reuse a recent one) -> /r/<id>
  GET  /r/<id>                the report; fills in live while it runs
  GET  /r/<id>/events         server-sent events for a running report
  GET  /r/<id>/body           the report body fragment
  GET  /r/<id>/claim/<n>      one claim card fragment
  GET  /r/<id>/snap/<file>    a snapshot image
  GET  /r/<id>/view.json      snapshot manifest for the in-page viewer
  GET  /api/r/<id>            the report as JSON
  GET  /api/check?url=        run a check synchronously, JSON (for scripts)
  GET  /healthz
"""
import json
import re
from dataclasses import asdict
from pathlib import Path

from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, send_from_directory, url_for)

from app.config import Config
from app.jobs import Jobs
from app.llm import FakeLLM, OpenAICompatLLM
from app.pipeline import Checker
from app.search import NoSearch, OpenRouterWebSearch, SearxngSearch
from app.store import Store

SNAP_NAME = re.compile(r'^[a-z0-9-]+\.(?:png|jpg)$')


def build_checker(cfg: Config) -> Checker:
    llm = (OpenAICompatLLM(cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model)
           if cfg.llm_base_url and cfg.llm_model else FakeLLM())
    return Checker(cfg, llm, build_search(cfg), snapshotter=build_snapshotter(cfg))


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
               sync_jobs: bool = False, cfg: Config | None = None) -> Flask:
    app = Flask(__name__)
    cfg = cfg or Config()
    factory = checker_factory or (lambda: build_checker(cfg))
    store = store or Store(Path(cfg.data_dir) / 'claimcheck.db')
    store.mark_interrupted()
    jobs = Jobs(store, factory, sync=sync_jobs)
    snap_root = Path(cfg.data_dir).resolve() / 'snapshots'
    app.config.update(FAKE_LLM=not (cfg.llm_base_url and cfg.llm_model), STORE=store, JOBS=jobs)

    def _loaded(rid: str):
        loaded = store.load(rid)
        if not loaded:
            abort(404)
        return loaded

    @app.get('/')
    def index():
        return render_template('index.html', fake=app.config['FAKE_LLM'], recent=store.recent(10))

    @app.get('/check')
    def check():
        url = (request.args.get('url') or '').strip()
        if not url:
            return render_template('index.html', fake=app.config['FAKE_LLM'], recent=store.recent(10),
                                   error='Paste an article URL to check.'), 400
        if not request.args.get('fresh') and cfg.cache_seconds > 0:
            cached = store.latest_for(url, cfg.cache_seconds)
            if cached:
                return redirect(url_for('report', rid=cached, cached=1), 303)
        return redirect(url_for('report', rid=jobs.start(url)), 303)

    @app.get('/r/<rid>')
    def report(rid):
        r, meta = _loaded(rid)
        return render_template('report.html', r=r, meta=meta, rid=rid,
                               cached=bool(request.args.get('cached')))

    @app.get('/r/<rid>/body')
    def report_body(rid):
        r, meta = _loaded(rid)
        return render_template('_body.html', r=r, meta=meta, rid=rid)

    @app.get('/r/<rid>/claim/<int:cid>')
    def claim_card(rid, cid):
        r, _ = _loaded(rid)
        claim = next((c for c in r.claims if c.id == cid), None)
        if not claim:
            abort(404)
        return render_template('_claim.html', c=claim, r=r, rid=rid)

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
        r, meta = _loaded(rid)
        return jsonify({**asdict(r), 'id': rid, 'created_at': meta['created_at']})

    @app.get('/api/check')
    def api_check():
        url = (request.args.get('url') or '').strip()
        if not url:
            return jsonify({'error': 'url is required'}), 400
        rid = jobs.start(url)
        jobs.wait(rid)
        r, meta = store.load(rid)
        return jsonify({**asdict(r), 'id': rid, 'created_at': meta['created_at']})

    @app.get('/healthz')
    def healthz():
        return {'ok': True}

    return app
