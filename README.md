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
fetch article ─► passages + cited links          app/fetch.py
      │
model: extract claims (exact spans)               app/prompts.py, app/llm.py
      │
code: span must be verbatim; value in span;       app/claims.py
      figures no claim covers → "not checked"
      │
search (SearXNG) + cited links ─► fetch sources   app/search.py, app/fetch.py
      │  each source tiered official/reference/open by host
model: judge one claim against passages           app/pipeline.py
      │
code: quote on the cited page? figure in quote?   app/verify.py
      date for "outdated"? else → unverified
      │
report: counts, tier mix, text-fragment links     app/templates/report.html
```

Model output never counts until code has checked it. The summary is counting.

Ported ideas from articles-ai: passage extraction and focused excerpts
(`research.py`), the quote-held-to-the-page rule (`supersession._verify`),
label-boundary domain matching and source tiers.

## Run

```bash
uv sync
uv run python -m pytest -q
uv run python -m app            # http://127.0.0.1:8050 — offline FakeLLM
```

Real checks — any OpenAI-compatible endpoint (the fleet's LiteLLM works):

```bash
export CC_LLM_BASE_URL=https://openrouter.ai/api   # or the LiteLLM URL, no /v1
export CC_LLM_API_KEY=...
export CC_LLM_MODEL=...
export CC_SEARXNG_URL=http://127.0.0.1:8888        # optional; self-hosted only
export CC_OFFICIAL_DOMAINS=dol.gov,irs.gov,treasury.gov,bls.gov
export CC_REFERENCE_DOMAINS=wikipedia.org,reuters.com,apnews.com
```

Without `CC_SEARXNG_URL` each claim is judged only against the links the
article itself cites — still a useful mode ("does the citation say that?").

## Status

Built: fetch with SSRF guard, passage parsing, claim validation and
"not checked" figures, tiering, the receipt verifier, OpenAI-compatible client
plus offline fake, synchronous report page and JSON API, tests (23).

Known gaps:
- The SSRF guard resolves then connects separately, so DNS rebinding is not
  closed. Pin the connection to the checked address before exposing this
  publicly.
- Reports run synchronously; a 15-claim page takes as long as its slowest
  judge call.
- No caching between requests.

## Hackathon day plan

| Time | Work |
|---|---|
| 0:00–0:45 | Point at a real model; tune `EXTRACT_SYSTEM` on 3 saved articles until spans validate |
| 0:45–2:00 | SearXNG (Docker) or an alternative search backend; check source quality per tier |
| 2:00–3:30 | Judge prompt tuning; measure receipt failure rate and log why each failed |
| 3:30–5:00 | Stream results (SSE or a polling job id) so claims colour in as they finish |
| 5:00–6:00 | "Cited-link audit" view: for each link the article cites, does it say what the article says? |
| 6:00–7:00 | Bookmarklet (`javascript:location='…/check?url='+encodeURIComponent(location)`), request cache |
| 7:00–8:00 | Prepare 3 demo URLs + a recorded fallback run; rehearse |

Demo: a typical SEO article → click a green receipt (source opens highlighted)
→ click a red one → show an unverified claim: "it would rather say I don't know
than guess."
