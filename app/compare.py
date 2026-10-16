"""What changed between two checks of the same article.

Claims are matched by their normalised span (the article's own words), so a
claim the extractor phrased identically both times is compared verdict to
verdict; anything else is listed as new or no longer checked.
"""
from app.claims import norm
from app.pipeline import Report


def compare(old: Report, new: Report) -> dict:
    before = {norm(c.span): c for c in old.claims}
    changed, same, added = [], [], []
    for c in new.claims:
        prev = before.pop(norm(c.span), None)
        if prev is None:
            added.append(c)
        elif prev.verdict != c.verdict:
            changed.append((prev, c))
        else:
            same.append((prev, c))
    return {'changed': changed, 'same': same, 'added': added, 'dropped': list(before.values())}
