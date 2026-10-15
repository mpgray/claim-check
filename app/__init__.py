"""Claim Check: paste an article URL, get a verdict with a receipt per claim.

Routes
  GET  /             the form
  GET  /check?url=   the report (GET so a report URL can be shared)
  GET  /api/check?url=   the same report as JSON
  GET  /healthz
"""
from dataclasses import asdict

from flask import Flask, jsonify, render_template, request

from app.config import Config
from app.llm import FakeLLM, OpenAICompatLLM
from app.pipeline import Checker
from app.search import NoSearch, SearxngSearch


def build_checker(cfg: Config) -> Checker:
    llm = (OpenAICompatLLM(cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model)
           if cfg.llm_base_url and cfg.llm_model else FakeLLM())
    search = SearxngSearch(cfg.searxng_url) if cfg.searxng_url else NoSearch()
    return Checker(cfg, llm, search)


def create_app(checker_factory=None) -> Flask:
    app = Flask(__name__)
    cfg = Config()
    factory = checker_factory or (lambda: build_checker(cfg))
    app.config['FAKE_LLM'] = not (cfg.llm_base_url and cfg.llm_model)

    @app.get('/')
    def index():
        return render_template('index.html', fake=app.config['FAKE_LLM'])

    def _run():
        url = (request.args.get('url') or '').strip()
        if not url:
            return None
        # A fresh Checker per request: its page cache is per report.
        return factory().run(url)

    @app.get('/check')
    def check():
        report = _run()
        if report is None:
            return render_template('index.html', fake=app.config['FAKE_LLM'],
                                   error='Paste an article URL to check.'), 400
        return render_template('report.html', r=report)

    @app.get('/api/check')
    def api_check():
        report = _run()
        if report is None:
            return jsonify({'error': 'url is required'}), 400
        return jsonify(asdict(report))

    @app.get('/healthz')
    def healthz():
        return {'ok': True}

    return app
