"""The CSV records example in examples/ works as documented."""
import sys
from pathlib import Path

from app.claims import Claim
from app.pipeline import Report

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'examples' / 'records_csv'))
from records_csv import CsvRecords  # noqa: E402

CSV = Path(__file__).resolve().parents[1] / 'examples' / 'records_csv' / 'records.csv'


def test_a_claim_naming_a_record_gets_it_as_one_decisive_document():
    plugin = CsvRecords(str(CSV))
    claim = Claim(id=1, span='The Example Widget sells for $24.99 in most stores.', kind='figure', value='$24.99')
    [doc] = plugin.documents(claim, Report(url='https://a.example/'))
    assert doc.decisive and doc.url == 'https://example.com/widget' and doc.date == '2026-01-01'
    assert doc.text.splitlines() == ['Example Widget price: $19.99', 'Example Widget weight: 250 grams']
    other = Claim(id=2, span='Gadgets are popular this year.', kind='other')
    assert plugin.documents(other, Report(url='https://a.example/')) == []
