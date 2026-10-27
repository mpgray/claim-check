"""Example plug-in: records from a CSV file, as decisive sources.

    CC_PLUGINS=records_csv:CsvRecords
    CC_RECORDS_CSV=/path/to/records.csv
    (run with this folder on PYTHONPATH, or package it with an entry point)

The CSV has a header row: name,label,value,url,as_of,decisive

    name      what the record is about, as articles name it ("Dune: Awakening")
    label     the fact ("release date")
    value     its value ("June 10, 2025")
    url       where a reader can see it
    as_of     ISO date the value is as of (optional)
    decisive  1 if this record settles a claim on its own (optional)

A claim whose sentence names a record's `name` gets that name's records as
one document, one line per fact ("Dune: Awakening release date: June 10,
2025"). The model must still quote a line word for word, and the claim's
figure must be in the quote, exactly as for a web page.
"""
import csv
import os
from collections import defaultdict

from app.claims import mentions
from app.plugins import Document


class CsvRecords:
    def __init__(self, path: str | None = None):
        self.by_name: dict[str, list[dict]] = defaultdict(list)
        with open(path or os.environ['CC_RECORDS_CSV'], newline='', encoding='utf-8') as fh:
            for row in csv.DictReader(fh):
                if row.get('name') and row.get('label') and row.get('value'):
                    self.by_name[row['name'].strip()].append(row)

    def documents(self, claim, report):
        out = []
        for name, rows in self.by_name.items():
            if not mentions(claim.span, [name]):
                continue
            lines = [f"{name} {r['label'].strip()}: {r['value'].strip()}" for r in rows]
            out.append(Document(
                url=rows[0].get('url') or '', title=f'{name} (records)', text='\n'.join(lines),
                date=max((r.get('as_of') or '' for r in rows), default=''),
                decisive=any((r.get('decisive') or '').strip() in ('1', 'true', 'yes') for r in rows)))
        return out
