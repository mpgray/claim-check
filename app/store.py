"""Reports on disk: one SQLite row per check, the report as JSON.

A report has a stable id so its URL can be shared, its snapshots have a
home, and a second check of the same article within the cache window is the
first report rather than another set of model calls.
"""
import json
import secrets
import sqlite3
import time
from dataclasses import asdict, fields
from pathlib import Path

from app.claims import Claim
from app.pipeline import Report

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,          -- as submitted
    final_url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,       -- running | done | error
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_url ON reports (url, created_at);
CREATE TABLE IF NOT EXISTS feedback (
    report_id TEXT NOT NULL,
    claim_id INTEGER NOT NULL,
    vote TEXT NOT NULL,         -- up | down
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS feedback_claim ON feedback (report_id, claim_id);
"""

_REPORT_FIELDS = {f.name for f in fields(Report)}
_CLAIM_FIELDS = {f.name for f in fields(Claim)}


def report_to_json(report: Report) -> str:
    return json.dumps(asdict(report))


def report_from_dict(data: dict) -> Report:
    claims = [Claim(**{k: v for k, v in c.items() if k in _CLAIM_FIELDS})
              for c in data.get('claims') or []]
    kept = {k: v for k, v in data.items() if k in _REPORT_FIELDS and k != 'claims'}
    return Report(claims=claims, **kept)


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    def _db(self) -> sqlite3.Connection:
        # A connection per call: jobs write from their own threads.
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def create(self, url: str) -> str:
        rid = secrets.token_urlsafe(8).replace('-', 'x').replace('_', 'y')
        now = time.time()
        with self._db() as db:
            db.execute('INSERT INTO reports (id, url, status, created_at, updated_at, json) '
                       'VALUES (?, ?, ?, ?, ?, ?)',
                       (rid, url, 'running', now, now, report_to_json(Report(url=url))))
        return rid

    def save(self, rid: str, report: Report) -> None:
        with self._db() as db:
            db.execute('UPDATE reports SET final_url=?, title=?, status=?, updated_at=?, json=? '
                       'WHERE id=?',
                       (report.url, report.title, report.status, time.time(),
                        report_to_json(report), rid))

    def load(self, rid: str) -> tuple[Report, dict] | None:
        with self._db() as db:
            row = db.execute('SELECT * FROM reports WHERE id=?', (rid,)).fetchone()
        if not row:
            return None
        meta = {k: row[k] for k in ('id', 'url', 'status', 'created_at', 'updated_at')}
        return report_from_dict(json.loads(row['json'])), meta

    def latest_for(self, url: str, max_age: float) -> str | None:
        """A finished report for this URL younger than `max_age` seconds."""
        with self._db() as db:
            row = db.execute(
                "SELECT id FROM reports WHERE (url=? OR final_url=?) AND status='done' "
                'AND created_at>? ORDER BY created_at DESC LIMIT 1',
                (url, url, time.time() - max_age)).fetchone()
        return row['id'] if row else None

    def recent(self, limit: int = 10) -> list[dict]:
        with self._db() as db:
            rows = db.execute('SELECT id, url, final_url, title, status, created_at FROM reports '
                              'ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]

    def mark_interrupted(self) -> int:
        """Reports left 'running' by a previous process will never finish."""
        with self._db() as db:
            rows = db.execute("SELECT id, json FROM reports WHERE status='running'").fetchall()
            for row in rows:
                report = report_from_dict(json.loads(row['json']))
                report.status = 'error'
                report.error = report.error or 'The check was interrupted (the server restarted).'
                db.execute('UPDATE reports SET status=?, json=? WHERE id=?',
                           ('error', report_to_json(report), row['id']))
        return len(rows)

    def previous_for(self, rid: str) -> dict | None:
        """The last finished report for the same article before this one."""
        with self._db() as db:
            me = db.execute('SELECT url, final_url, created_at FROM reports WHERE id=?', (rid,)).fetchone()
            if not me:
                return None
            row = db.execute(
                "SELECT id, created_at FROM reports WHERE status='done' AND id<>? AND created_at<? "
                'AND (url IN (?, ?) OR final_url IN (?, ?)) ORDER BY created_at DESC LIMIT 1',
                (rid, me['created_at'], me['url'], me['final_url'], me['url'], me['final_url'])).fetchone()
        return dict(row) if row else None

    def add_feedback(self, rid: str, claim_id: int, vote: str, note: str = '') -> None:
        if vote not in ('up', 'down'):
            raise ValueError('vote must be up or down')
        with self._db() as db:
            db.execute('INSERT INTO feedback (report_id, claim_id, vote, note, created_at) '
                       'VALUES (?, ?, ?, ?, ?)', (rid, claim_id, vote, note[:1000], time.time()))

    def feedback_counts(self, rid: str) -> dict[int, dict]:
        with self._db() as db:
            rows = db.execute('SELECT claim_id, vote, COUNT(*) AS n FROM feedback WHERE report_id=? '
                              'GROUP BY claim_id, vote', (rid,)).fetchall()
        out: dict[int, dict] = {}
        for r in rows:
            out.setdefault(r['claim_id'], {'up': 0, 'down': 0})[r['vote']] = r['n']
        return out

    def all_feedback(self) -> list[dict]:
        """Every vote with the claim it was about, for tuning the prompts."""
        with self._db() as db:
            rows = db.execute('SELECT f.*, r.json FROM feedback f JOIN reports r ON r.id = f.report_id '
                              'ORDER BY f.created_at').fetchall()
        out = []
        for r in rows:
            report = report_from_dict(json.loads(r['json']))
            claim = next((c for c in report.claims if c.id == r['claim_id']), None)
            out.append({'report_id': r['report_id'], 'url': report.url, 'claim_id': r['claim_id'],
                        'vote': r['vote'], 'note': r['note'], 'created_at': r['created_at'],
                        'span': claim.span if claim else '', 'verdict': claim.verdict if claim else '',
                        'reason': claim.reason if claim else '',
                        'receipt': claim.receipt if claim else None})
        return out
