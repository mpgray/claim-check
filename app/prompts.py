"""The model calls. All return JSON; code checks everything they return."""

EXTRACT_SYSTEM = """EXTRACT every checkable factual claim from one section of an article.

Return JSON: {"subject": ..., "aliases": [...],
              "claims": [{"span": ..., "kind": ..., "value": ..., "query": ...}]}

- subject: the one specific thing the whole article is about (a game,
  product, person, place, organisation, event or law), named exactly as the
  article names it. "" if the article has no single subject.
- aliases: up to 3 other names the article itself uses for the subject.

List the sentences in the section that assert something a source could
confirm or refute: figures, dates, names, rules, how something works,
causes, comparisons, rankings, attributed quotes. Fill the quota below when
the section has that many; do not stop at the two or three most important.
Leave out headings, questions and sentences with no factual content.

- span: copy the claim's sentence EXACTLY as it appears in the article,
  character for character. Do not paraphrase, shorten or fix it.
- kind: one of figure, date, legal, quote, causal, other, opinion.
  Use opinion for judgement, advice or prediction; those are not checked.
- value: the exact number or date the claim turns on, copied from the span,
  or "" if none.
- query: a web search query that would find a primary source for it. It
  must name the subject whenever the claim is about the subject, even if the
  sentence itself does not (the sentence is read without the article). When
  the claim is about a current or time-bound figure, include the year it
  applies to (the article's date is given; use it when the claim gives none).

Up to {limit} claims for this section, spread across all of it."""

# The second extraction pass: the same task, worded to walk the section line
# by line. An identical repeat at temperature 0 mostly returns the same list.
EXTRACT_SECOND_PASS = """

Work through the section one sentence at a time, from the first line to the
last, and include each sentence that makes a factual assertion, even one that
seems minor or obvious."""

EXTRACT_USER = "ARTICLE TITLE: {title}\nARTICLE DATE: {article_date}\nSECTION {section} OF {sections}:\n\nARTICLE:\n{article}"

JUDGE_SYSTEM = """JUDGE one claim against the source passages provided.

Return JSON: {"verdict": ..., "url": ..., "quote": ..., "as_of": ..., "reason": ...}

- verdict: supported, contradicted, outdated, or unverified.
  - supported: a source states the claim, including its exact figure.
  - contradicted: a source states something incompatible with it.
  - outdated: it was true, but a source shows it has since changed.
  - unverified: the passages do not settle it. Say this rather than guess.
- url: the source URL the quote comes from, exactly as given.
- quote: copy the deciding sentence EXACTLY from that source's text. If the
  claim's figures or dates are in different places on that page, give up to
  three exact excerpts joined by " ... ". Every figure and date in the claim
  must be in the quote. Your verdict is discarded if any excerpt is not found
  word for word.
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

# Only for a claim already contradicted or outdated by a verified receipt.
# `verify.check_correction` refuses any figure or word it adds that the quote
# does not contain.
CORRECT_SYSTEM = """REWRITE one sentence so it agrees with its source.

A fact-checker found the sentence contradicted or outdated by the source
quote below. Return JSON: {"text": ...}

- text: the sentence with the smallest change that makes it agree with the
  quote: put the quote's figure, date or name where the sentence has the
  wrong one, and keep every other word. For outdated, say what it is now.
- Use only figures, dates and names that are in the quote or the sentence.
- "" if the quote does not say what the sentence should say instead."""

CORRECT_USER = "VERDICT: {verdict}\nSENTENCE: {span}\nSOURCE QUOTE: {quote}\nCHANGED ON: {as_of}"
