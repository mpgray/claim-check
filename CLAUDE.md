# claim-check

Paste an article URL; each factual claim gets a verdict with a receipt.
`README.md` has the pipeline and run instructions.

## The rule that governs this repo

**A model's output never counts until code has checked it.**

- A claim survives only if its `span` is verbatim article text
  (`app/claims.validate`).
- A verdict other than `unverified` survives only if its quote is word for
  word on a page that was actually fetched (`verify.find_excerpts`: the same
  words in the same order, case, whitespace and punctuation aside; up to three
  excerpts joined by "...", all within 5,000 characters; the receipt is the
  page's own text), contains every number and date of
  the claim's value when it is a figure (compared by value in `app/figures.py`:
  "100 million" is "100,000,000", each date of a range on its own, and read
  as the claim states it: "more than X" needs at least X, "about X" within
  10%, a plain X equal at the precision written; never a near miss), and `outdated` carries a date (`app/verify.check`). A failed check becomes
  `unverified` with the reason; it is never retried into a pass.
- A `contradicted` from a source older than the claim's moment (the span's
  year, else the article's date) becomes `unverified` (`verify.stale_contradiction`).
- The article's own site is never a source (`pipeline.site`).
- The article's subject is the model's proposal, kept only if the article's
  text names it (`claims.validate_subject`). Every search query names it, and
  a source page that names neither it nor a specific name from the claim
  (`claims.entities`) is dropped before judging (the claim's own citations
  are exempt). A page of fragments (under 25% of its text in sentences:
  Wikidata, catalogues) is not a source unless its tier is official (a chart
  history or certification table is the record itself). A refused receipt is kept on the
  claim and summarised in one plain sentence, never used.
- Fixes stay generic: this checks ANY article, so no topic-specific data
  (a game database, a curated per-topic domain list) is a source of truth.
- Source tier comes from the host, never from the model.
- Summary numbers are counts.
- An inflation conversion is computed by code from BLS CPI data
  (`app/inflation.py`), never judged by the model. It contradicts a claim only
  with an explicit base year, and a correct one never upgrades a claim alone.
- A snapshot highlights only text code already verified, matched with the
  same normalisation as `claims.norm`. A miss is shown as a miss; an image of
  a page the fetch did not see (bot check, consent wall) is never kept, and
  text the page hides (paywall fold) is reported as hidden, not captured.

Do not loosen `verify.check` to raise the supported rate. A low supported rate
with honest receipts is the product working.

## Before committing

```bash
uv run python -m pytest -q
```

Snapshot tests need `uv run playwright install chromium`; without it they skip.
