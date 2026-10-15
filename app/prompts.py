"""The two model calls. Both return JSON; code checks everything they return."""

EXTRACT_SYSTEM = """EXTRACT factual claims from an article.

Return JSON: {"claims": [{"span": ..., "kind": ..., "value": ..., "query": ...}]}

- span: copy the claim's sentence EXACTLY as it appears in the article,
  character for character. Do not paraphrase, shorten or fix it.
- kind: one of figure, date, legal, quote, causal, other, opinion.
  Use opinion for judgement, advice or prediction; those are not checked.
- value: the exact number or date the claim turns on, copied from the span,
  or "" if none.
- query: a web search query that would find a primary source for it.

Prefer specific, checkable claims (figures, dates, legal requirements,
attributed quotes) over general statements. At most {limit} claims."""

EXTRACT_USER = "ARTICLE:\n{article}"

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

Use only the passages below, never memory."""

JUDGE_USER = "CLAIM:\n{claim}\nSOURCES:\n{sources}"
