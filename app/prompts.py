"""The two model calls. Both return JSON; code checks everything they return."""

EXTRACT_SYSTEM = """EXTRACT factual claims from an article.

Return JSON: {"claims": [{"span": ..., "kind": ..., "value": ..., "query": ...}]}

- span: copy the claim's sentence EXACTLY as it appears in the article,
  character for character. Do not paraphrase, shorten or fix it.
- kind: one of figure, date, legal, quote, causal, other, opinion.
  Use opinion for judgement, advice or prediction; those are not checked.
- value: the exact number or date the claim turns on, copied from the span,
  or "" if none.
- query: a web search query that would find a primary source for it. When
  the claim is about a current or time-bound figure, include the year it
  applies to (the article's date is given; use it when the claim gives none).

Prefer specific, checkable claims (figures, dates, legal requirements,
attributed quotes) over general statements. At most {limit} claims."""

EXTRACT_USER = "ARTICLE DATE: {article_date}\nARTICLE:\n{article}"

JUDGE_SYSTEM = """JUDGE one claim against the source passages provided.

Return JSON: {"verdict": ..., "url": ..., "quote": ..., "as_of": ..., "reason": ...}

- verdict: supported, contradicted, outdated, or unverified.
  - supported: a source states the claim, including its exact figure.
  - contradicted: a source states something incompatible with it.
  - outdated: it was true, but a source shows it has since changed.
  - unverified: the passages do not settle it. Say this rather than guess.
- url: the source URL the quote comes from, exactly as given.
- quote: copy the deciding sentence EXACTLY from that source's text.
  Your verdict is discarded if the quote is not found verbatim.
- as_of: for outdated, the ISO date (YYYY-MM-DD) it changed; else "".
- reason: one plain sentence for a reader.

Dates: the article's date and each source's date (when known) are given. A
source written BEFORE the article cannot show that the article's current
figure is wrong; at most it shows an older figure. Answer unverified then,
not contradicted. A deciding quote that names its own year is judged by that
year.

Inflation: an inflation-adjusted equivalent in the claim ("equivalent to $X
in YEAR") is computed separately by code from official CPI data. Judge the
rest of the claim; do not answer unverified only because no source restates
the adjusted figure.

Use only the passages below, never memory."""

JUDGE_USER = "ARTICLE DATE: {article_date}\nCLAIM:\n{claim}\nSOURCES:\n{sources}"
