from app.claims import Claim
from app.pipeline import Report
from app.store import Store


def test_round_trip_keeps_claims_and_receipts(tmp_path):
    store = Store(tmp_path / 'db.sqlite3')
    rid = store.create('https://a.example/x')
    report = Report(url='https://a.example/x', title='X', status='done', date='2026-01-01',
                    claims=[Claim(id=1, span='The wage is $7.25 per hour.', kind='figure',
                                  value='$7.25', verdict='supported',
                                  receipt={'url': 'https://dol.gov', 'quote': 'q', 'tier': 'official'})])
    store.save(rid, report)
    loaded, meta = store.load(rid)
    assert loaded.claims[0].receipt['tier'] == 'official' and loaded.date == '2026-01-01'
    assert meta['status'] == 'done'
    assert store.latest_for('https://a.example/x', 60) == rid
    assert store.recent()[0]['title'] == 'X'


def test_running_reports_from_a_dead_process_are_marked(tmp_path):
    store = Store(tmp_path / 'db.sqlite3')
    rid = store.create('https://a.example/x')
    assert store.mark_interrupted() == 1
    report, meta = store.load(rid)
    assert meta['status'] == 'error' and 'interrupted' in report.error
    assert store.latest_for('https://a.example/x', 60) is None
