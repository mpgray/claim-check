"""Background checks with a replayable event log per report.

A check runs in its own thread. Each pipeline event saves the report and is
appended to an in-memory log, so a browser that connects late (or
reconnects) replays what it missed and then follows live.
"""
import threading
import time

from app.pipeline import Report, summarize
from app.store import Store

TERMINAL = ('done', 'error')
SAVE_ON = ('fetched', 'claims', 'claim', 'snapshots', 'done', 'error')


class Jobs:
    def __init__(self, store: Store, checker_factory, *, sync: bool = False):
        self.store, self.factory, self.sync = store, checker_factory, sync
        self._log: dict[str, list[tuple[str, dict]]] = {}
        self._cond = threading.Condition()

    def start(self, url: str) -> str:
        rid = self.store.create(url)
        with self._cond:
            self._log[rid] = []
        if self.sync:
            self._run(rid, url)
        else:
            threading.Thread(target=self._run, args=(rid, url), daemon=True,
                             name=f'check-{rid}').start()
        return rid

    def _run(self, rid: str, url: str) -> None:
        try:
            self.factory().run(url, on_event=lambda k, r, d: self._on(rid, k, r, d), report_id=rid)
        except Exception as exc:  # a crash is a report with an error, not a spinner forever
            loaded = self.store.load(rid)
            report = loaded[0] if loaded else Report(url=url)
            report.status, report.error = 'error', f'The check crashed: {exc}'[:300]
            self._on(rid, 'error', report, {'error': report.error})

    def _on(self, rid: str, kind: str, report: Report, data: dict) -> None:
        if kind in SAVE_ON:
            summarize(report)
            self.store.save(rid, report)
        event = {**data, 'counts': dict(report.counts), 'total': len(report.claims),
                 'status': report.status}
        with self._cond:
            self._log.setdefault(rid, []).append((kind, event))
            self._cond.notify_all()

    def events(self, rid: str, after: int = 0, keepalive: float = 15.0):
        """Yield (index, kind, data) from `after`, then live, until the job ends.
        Yields (None, 'ping', {}) while waiting so proxies keep the stream open."""
        with self._cond:
            known = rid in self._log
        if not known:
            # Not run by this process: finished earlier, or interrupted.
            loaded = self.store.load(rid)
            status = loaded[1]['status'] if loaded else 'error'
            yield 0, ('done' if status == 'done' else 'error'), {'status': status}
            return
        i = after
        while True:
            with self._cond:
                log = self._log.get(rid, [])
                if i >= len(log):
                    self._cond.wait(timeout=keepalive)
                    log = self._log.get(rid, [])
                batch = log[i:]
            if not batch:
                yield None, 'ping', {}
                continue
            for kind, data in batch:
                i += 1
                yield i, kind, data
                if kind in TERMINAL:
                    return

    def wait(self, rid: str, timeout: float = 600) -> None:
        """Block until a job finishes (tests and the synchronous API)."""
        end = time.monotonic() + timeout
        for _, kind, _ in self.events(rid):
            if kind in TERMINAL or time.monotonic() > end:
                return
