# Claim Check

Paste an article URL. Every factual claim gets a verdict — **supported**,
**contradicted**, **outdated**, **unverified** or **opinion** — and every
supported, contradicted or outdated verdict carries a **receipt**: a quote from
the source, verified by code to be on that page, linked so the browser opens
the source with that sentence highlighted.

> Every verdict comes with a receipt. A model that cannot produce one gets
> "unverified", never "probably fine".

## How it works

```
fetch article ─► readable body (trafilatura)      app/fetch.py
      │          + declared date, cited links     app/dates.py
      │            tied to their passage
model: extract claims, 30k-char chunks (≤200k)    app/prompts.py, app/llm.py
      │  and the article's subject; code keeps the
      │  subject only if the article's text names it
      │
code: span must be verbatim; value in span;       app/claims.py
      figures no claim covers → "not checked"
      │
claim's own citations (Wikipedia footnotes are   app/search.py, app/fetch.py
      followed to the reference list), then search
      results official-first, then other cited links
      (every query names the subject) ─► fetch sources
      (HTML or PDF); a source that never names the
      subject is dropped, and one exact-phrase retry
      search refills the slot
      │  the article's own site is never a source
      │  each source tiered official/reference/open by host
code: inflation conversions computed from BLS     app/inflation.py
      CPI-U ("$61 ... equivalent to $104 in 2025")
model: judge one claim against passages + dates   app/pipeline.py
      │
code: quote on the cited page? figure in quote?   app/verify.py
      date for "outdated"? source older than the
      claim for "contradicted"? else → unverified
      │
snapshot: Chromium renders the article and each   app/snapshot.py,
      source, paints claims/quotes, saves PNGs    app/static/highlight.js
      │
report: stored, live-streamed, shareable /r/<id>  app/store.py, app/jobs.py,
                                                  app/templates/
```

Model output never counts until code has checked it. The summary is counting.

### Snapshots

After judging, a headless Chromium (Playwright) opens the article and paints
every claim in its verdict colour, then opens each receipt's source and paints
the quote. The whole page is saved as stacked JPEG tiles plus a crop per
claim, in `data/snapshots/<report id>/`. Clicking a claim's thumbnail opens an
in-page viewer at that claim, with numbered markers on every highlight,
prev/next (or arrow keys) and an actual-size toggle. The live `#:~:text=` link
can break when a page changes; the snapshot does not.

- Highlights use the CSS Custom Highlight API, so the page's markup and
  layout are untouched. Matching mirrors `claims.norm`.
- A highlight counts only if the browser shows that text at that spot
  (`elementFromPoint`). Text in the DOM but under a paywall fold or overlay is
  reported as hidden; text the rendered page lacks is reported as not found.
- If the browser is served a different page than the fetch got (a bot check,
  a consent wall), no image is kept and the report says so.
- Every browser request goes through the same public-address check as the
  fetch. Fixed overlays are hidden before capture; nothing is clicked.
- PDF sources are quoted from their text layer; they get no image.

### Computed checks

An inflation conversion is arithmetic, not a fact a source restates, so code
computes it from the BLS CPI-U annual averages (FRED as fallback; cached in
`data/cpi_us.json` for a week). The base year is the one the claim states,
else the nearest earlier year in the text, and the report says which. A wrong
conversion contradicts the claim only when the base year is explicit; a
correct one never upgrades a claim by itself.

### Source tiers

`official` and `reference` come from built-in host rules (government
suffixes worldwide, intergovernmental bodies, bodies of record; universities,
journals, papers of record, fact-checkers) plus `CC_OFFICIAL_DOMAINS` /
`CC_REFERENCE_DOMAINS`. `CC_DEFAULT_TIERS=0` keeps only yours. Archived copies
(`web.archive.org`) tier as the site they archive. `/sources` lists the rules.

### Report extras

- **Citations:** what each claim's own cited links showed when read.
- **Compare:** verdicts that changed since the previous check of the article.
- **Feedback:** thumbs up/down per verdict; `/api/feedback` exports them with
  the claim, reason and receipt for prompt tuning.
- **PDF:** `/r/<id>/report.pdf`. **Share:** Open Graph tags with the counts.
- **Bookmarklet** on the home page.

Ported ideas from articles-ai: passage extraction and focused excerpts
(`research.py`), the quote-held-to-the-page rule (`supersession._verify`),
label-boundary domain matching and source tiers.

## Run

```bash
uv sync
uv run playwright install chromium   # once, ~300 MB; for snapshots
uv run python -m pytest -q
uv run python -m app            # http://127.0.0.1:8050 — offline FakeLLM
```

Reports and snapshots go to `data/` (`CC_DATA_DIR`). A check of the same URL
within an hour reuses the stored report (`CC_CACHE_SECONDS`; `?fresh=1` on
`/check` forces a new one). `CC_SNAPSHOTS=0` turns snapshots off.
`/api/check?url=` still runs synchronously and returns JSON.

### Demo setup: OpenRouter + Gemma 4

One key is enough. With only `CC_OPENROUTER_API_KEY` set, the chat model
defaults to **`google/gemma-4-31b-it`** and search uses OpenRouter's web plugin.

```bash
cp .env.example .env            # fill in the key, then:
set -a; . ./.env; set +a
uv run python -m scripts.check_backend   # probe model + search; run before the demo
uv run python -m app
```

- `CC_LLM_MODEL` overrides the model; `google/gemma-4-31b-it-20260402` pins the
  exact release.
- `CC_SEARCH_MODEL` pins the search model separately. If the probe says the
  web plugin "did not run" on Gemma, set this to a model that supports it —
  the judging still runs on Gemma.
- If `response_format` is refused by the serving provider, the client drops it
  after the first 400 and parses JSON from the text.
- A search failure is shown on the report, never silently turned into a page
  of "unverified".

Any other OpenAI-compatible endpoint (the fleet's LiteLLM) works with
`CC_LLM_BASE_URL` / `CC_LLM_API_KEY` / `CC_LLM_MODEL`; a self-hosted SearXNG
with `CC_SEARXNG_URL`. Without any search backend each claim is judged only
against the links the article itself cites ("does the citation say that?").

## Status

Built: fetch with SSRF guard, readable-body extraction, page dates, PDF
sources, claim validation and "not checked" figures, per-claim citations,
tiering, the receipt verifier with the stale-source rule, OpenAI-compatible
client plus offline fake, OpenRouter web-plugin search, Gemma 4 default,
backend probe, stored reports with live progress (SSE), snapshots with
highlights, JSON API, tests.

Known gaps:
- The SSRF guard resolves then connects separately (in the fetch and in the
  snapshot browser), so DNS rebinding is not closed. Pin the connection to
  the checked address before exposing this publicly. No rate limiting either.
- Sites that block headless browsers (Cloudflare) get no snapshot; the
  report says so.
- Claim extraction varies run to run (the model picks different claims).
- Paywalled sources are quoted from the server-rendered text; their
  snapshot reports the quote as hidden.
- Jobs run in-process threads; a restart marks running reports as
  interrupted.

## Hackathon day plan

| Time | Work |
|---|---|
| 0:00–0:45 | `scripts.check_backend` green on Gemma 4; tune `EXTRACT_SYSTEM` on 3 saved articles until spans validate |
| 0:45–2:00 | Check OpenRouter web results per tier; set `CC_OFFICIAL_DOMAINS` for the demo topics |
| 2:00–3:30 | Judge prompt tuning on Gemma; measure receipt failure rate (verbatim quoting is the hard part for a 31B model) and log why each failed |
| 3:30–5:00 | Stream results (SSE or a polling job id) so claims colour in as they finish |
| 5:00–6:00 | "Cited-link audit" view: for each link the article cites, does it say what the article says? |
| 6:00–7:00 | Bookmarklet (`javascript:location='…/check?url='+encodeURIComponent(location)`), request cache |
| 7:00–8:00 | Prepare 3 demo URLs + a recorded fallback run; rehearse |

Demo: a typical SEO article → click a green receipt (source opens highlighted)
→ click a red one → show an unverified claim: "it would rather say I don't know
than guess."
