# How Retrieval Works

A walkthrough of the only active retrieval strategy: exact lexical search over
SQLite FTS5. Every number here was measured against the live index —
40,551 documents, 482,130 chunks, `section-table-genq-columns-v5`.

Read this before changing `search.py`. Several constants look arbitrary and are
not; several look load-bearing and are not.

## The shape of the index

`indexer.py` parses each Epic HTML page into chunks and writes two tables:

```sql
CREATE TABLE chunks (chunk_id, doc_id, chunk_index, category,
                     heading_path, text, token_count, text_hash);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    chunk_id, source_path, title, category, heading_path, text);
```

One page becomes many chunks. Column definitions get one chunk each, from
`column_parser.py`; other sections are chunked by heading. The live split:

| Category | Chunks | Share |
|---|---:|---:|
| `column_info` | 356,056 | 73.9% |
| `table_data` | 80,081 | 16.6% |
| `metadata` | 40,551 | 8.4% |
| `table_generic` | 5,442 | 1.1% |

The median chunk is **333 characters** — roughly 83 tokens. That number governs
everything downstream: a chunk this small can only contain a handful of distinct
terms, so any query requiring many terms to co-occur in one chunk will fail.

## The pipeline

`search_ranked_chunks()` is six steps.

### 1. Tokenize

`search_tokens()` splits on `\w+`, lowercases, drops a 40-word stopword list,
drops tokens under three characters unless they carry a digit
(`MIN_FTS_TOKEN_CHARS`), strips a trailing `s` from words over four characters,
dedupes, and keeps the **8 rarest** survivors (`MAX_FTS_QUERY_TOKENS`).

Both rules exist because of measured failures, not taste.

**Short tokens are contraction debris.** `\w+` splits `I'm` on the apostrophe
and leaves `m`, which step 2 turns into the prefix term `"m"*` — spanning
511,742 chunk-term rows against 651 for `"hyperspace"*`. 31 of the 50 benchmark
queries produced such a token (`m`, `in`, `as`, `it`, `id`) and they were the
slowest 31. Dropping them cut median latency from 599 ms to 228 ms. Tokens with
a digit are exempt, so `R1` and the `30` in "30-day readmission" survive.

**Rarity beats position.** Truncating to the first 8 tokens ranks by where a
word sits in the sentence, which is unrelated to how much it narrows the search.
`select_query_tokens()` ranks by document frequency instead, and identifiers —
anything containing `_` or a digit — are kept unconditionally without a lookup,
because they are the most selective terms this corpus has.

The frequencies are exact, from the FTS index's own vocabulary
(`document_frequency_lookup()`), via an `fts5vocab` table created in `temp` so
the index stays open read-only and needs no rebuild. The count is taken over the
prefix range rather than the exact term, because a prefix term is what step 2
emits: `"admission"*` also reaches `admissions`. Without a provider the function
falls back to positional truncation, so callers holding no index still work.

On a real benchmark query the difference is not subtle:

```
"I'm reviewing the incremental cleanup feed for Hyperspace access.
 What does A0H_DELETE contain, and what field identifies each row?"

by position:  m, reviewing, incremental, cleanup, feed, hyperspace,
              access, a0h_delete
by rarity:    a0h_delete(0), reviewing(64), hyperspace(651), cleanup(698),
              feed(1050), field(2997), access(5690), each(9049)
dropped:      row(9911), identifie(23773), incremental(36742), contain(63172)
```

`incremental` looks like a content word and is one of the commonest terms in the
index — it is the `Load Frequency::` enum value stamped on nearly every metadata
chunk. Positional truncation kept it and dropped `field`; frequency knows
better. `a0h_delete` had been surviving at position eight by luck.

38 of 50 queries still exceed the cap, so this selection runs on most of them.

### 2. Build the FTS query

`fts5_query()` emits one disjunction:

```python
" OR ".join(terms)    # any token
```

Tokens containing `_` or consisting of digits become exact terms (`"a0h_delete"`);
everything else becomes a prefix term (`"access"*`).

A conjunctive pass used to run first, and its rows were preferred on the theory
that a chunk containing every term beats one containing any term. It does — it
just never happens. **The strict pass returned zero rows for 50 of 50 benchmark
queries.** Not "rarely useful": never useful, on any analyst-phrased question,
because requiring eight tokens to co-occur inside a 333-character chunk is close
to impossible. It was removed.

Be precise about what removing it bought. Measured across the benchmark, the
strict pass cost **13 ms per query** against the disjunction's **133 ms** — it
was dead weight, not the latency tail. Deleting a branch that could never fire
is the reason; the 9% is a side effect.

Requiring a *subset* — the two or three rarest terms — is plausible, and cheap to
build now that step 1 already ranks by frequency. That is a precision change with
its own delta, and it has not been tried.

The latency tail comes from step 1, not from here: a junk prefix term scans an
enormous posting list, and dropping those terms moved the whole distribution.

```
                    original   tokenizer   no AND pass
median                599 ms      228 ms        199 ms
p95                 2,334 ms    1,614 ms      1,507 ms
```

The per-token frequency lookups added by step 1 did not cost this back — the
terms expensive enough to matter are exactly the ones now dropped before any
lookup happens.

`max` is deliberately absent. It lands on whichever query runs first and pays the
cold read on a 1.1 GB index, so it moves by seconds between runs that are
otherwise identical. Read median and p95; treat a change in `max` as unattributed
until a warm rerun reproduces it.

### 3. Extract a document hint

`document_hint()` regex-matches `\b[A-Z][A-Z0-9_]{2,}\b`, drops four known
false positives (`EHI`, `ETL`, `INI`, `SQL`), also accepts `<word> table`, and
returns the longest candidate, preferring names containing `_`.

**This is the single most important component in the system, and it is not the
FTS query.** The hint fires on 11 of 24 benchmark queries, and those overlap
almost exactly with the queries that succeed. `named_table_lookup` scores
**Hit@5 = 1.000**; every query in it names a table in capitals.

It also misfires. Q08 asks "why do the mapping and ABN tables have a `LINE`
field" and the hint returns `LINE`, boosting every document whose path or title
contains that word. Q20's hint is `ABN`, a prefix shared by a dozen tables.
A wrong hint is worse than none, because step 4 sorts by it before score.

### 4. Rank

```sql
ORDER BY document_rank, bm25(chunks_fts, 0.0, 8.0, 10.0, 0.5, 5.0, 1.0)
```

`document_rank` is 0 when the row's `source_path` or `title` matches the hint
and 1 otherwise — so **a hint match outranks every non-match regardless of
score**. Only within those two groups does BM25 order anything.

The weights map positionally onto the FTS columns:

| Column | Weight | Effect |
|---|---:|---|
| `chunk_id` | 0.0 | ignored — it is a hash |
| `source_path` | 8.0 | filename is strong evidence |
| `title` | 10.0 | strongest signal |
| `category` | 0.5 | nearly ignored |
| `heading_path` | 5.0 | `TABLE > Column-Information > COL` |
| `text` | 1.0 | baseline |

FTS5 BM25 returns *negative* scores, better being more negative, which is why
`ORDER BY score` ascending is correct and why raw scores look wrong in reports.

### 5. Widen, then cap per document

The pool is `top_k * CANDIDATE_POOL_MULTIPLIER` (4×), because a diversity filter
needs candidates to discard. Then `apply_document_diversity()` keeps at most
`MAX_CHUNKS_PER_DOCUMENT` (3) chunks per document in a first pass, collects the
rest as displaced, and backfills from them if the budget is unfilled.

The comment in `search.py` records why: a probe for "patient primary care
provider" returned five chunks of `DM_CANCER_PATIENT_HX` out of eight. The cap
trades ordering, not recall — a query that genuinely matches one document still
fills the budget, just via backfill.

The document named by the hint is **exempt** from the cap. Asking about
`ABN_ORDERS` should return `ABN_ORDERS`, and breadth is not wanted there.

The cap has a failure mode at `k=5`. Benchmark Q15 retrieves three chunks of
`ZC_MYC_OCCHX_OCCUPATION` at ranks 1–3 — all irrelevant. The cap permitted
exactly three, so one wrong document consumed 60% of the top-5 budget.

### 6. Hydrate

`retrieve_documentation_context()` re-reads each ranked chunk by ID to get full
text, attaches rank and score, and returns them with the index version.

## What this is good and bad at

Measured on the 50-query benchmark, document Hit@5 by failure bucket:

| Bucket | Hit@5 | Why |
|---|---:|---|
| `named_table_lookup` | **1.000** | Hint fires; exact identifier match |
| `named_column_schema` | 0.600 | Column names are not unique across 40K docs |
| `cross_table_synthesis` | 0.400 | Works when a table is named |
| `business_concept_discovery` | **0.091** | No lexical overlap to match on |

The pattern is one thing: **this system retrieves identifiers, not meaning.**

Where it fails, it fails plausibly, which is worse than failing loudly:

- *"which field tells me the batch number"* → `AP_CYCLE_CHECK_SPLITS`,
  `DOC_PMTPOSTING_BATCHES`. Excellent lexical matches for `split` and `batch`;
  wrong documents. Vocabulary collision at corpus scale.
- *"for a ZC reporting dimension, which fields provide the normal label..."* →
  `DATE_DIMENSION`. It matched `dimension`, the least meaningful word present.
- *"break down personal-injury cases by what happened, what the person was
  doing, and where the injury occurred"* → targets are `ZC_XPR_INJ_MECHANISM`,
  `ZC_XPR_INJ_ACTIVITY`, `ZC_XPR_INJURY_SITE`. "What happened" *is* mechanism,
  "what the person was doing" *is* activity. Zero shared terms, so zero score.

That last case is the honest argument for embeddings, and it is worth being
precise about the scope: the deficit is concentrated in
`business_concept_discovery`. On identifier lookup, lexical search is already at
1.000 and embeddings would have to work not to regress it — dense vectors blur
rare tokens like `ABN_STATUS_C`, which is exactly what this corpus is made of.
Expect hybrid, not replacement.

## Known defects, unfixed on purpose

The two tokenizer defects and the strict AND pass are fixed; see steps 1 and 2.
These two remain, deliberately, so each is readable as its own delta:

1. **Validate the document hint.** Confirm the candidate matches a real
   `source_path` before letting it override ranking; `LINE` and `ABN` currently
   do not. This is the highest-value one left — step 4 sorts by the hint before
   score, so a wrong hint outranks every correct result.
2. **Reconsider `MAX_CHUNKS_PER_DOCUMENT = 3` at `k=5`.** Three chunks of one
   wrong document is most of a top-5 budget.

Fixing the tokenizer moved document Hit@5 from 0.488 to 0.512 and recall@5 from
0.427 to 0.463 while holding `named_table_lookup` at 1.000. Document MRR fell
0.476 → 0.462: a wider token set surfaces more correct documents but not always
at the same rank. Hit and recall rising while MRR dips is the expected shape of
a recall change, and it is the trade this system wants — a document that was
absent is now retrievable.

Removing the strict AND pass changed **no quality metric at any level**, to three
decimals. That is the expected result for deleting a branch that returned zero
rows on every query, and it is the evidence that it really did.

Numbers here are comparable only within one `chunker_version`
(`index_contract.py`). Re-measure with `agent-harness-eval --suite retrieval`
after any change to chunk boundaries.
