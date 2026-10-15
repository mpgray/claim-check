# claim-check

Paste an article URL; each factual claim gets a verdict with a receipt.
`README.md` has the pipeline and run instructions.

## The rule that governs this repo

**A model's output never counts until code has checked it.**

- A claim survives only if its `span` is verbatim article text
  (`app/claims.validate`).
- A verdict other than `unverified` survives only if its quote is on a page
  that was actually fetched, contains the claim's figure when it is a figure,
  and `outdated` carries a date (`app/verify.check`). A failed check becomes
  `unverified` with the reason; it is never retried into a pass.
- Source tier comes from the host, never from the model.
- Summary numbers are counts.

Do not loosen `verify.check` to raise the supported rate. A low supported rate
with honest receipts is the product working.

## Before committing

```bash
uv run python -m pytest -q
```
