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
strips a trailing `s` from words over four characters, dedupes, and **takes the
first 8** (`MAX_FTS_QUERY_TOKENS`).

Two problems live here, both visible on a real benchmark query:

```
"I'm reviewing the incremental cleanup feed for Hyperspace access.
 What does A0H_DELETE contain, and what field identifies each row?"

tokens: ['m', 'reviewing', 'incremental', 'cleanup', 'feed',
         'hyperspace', 'access', 'a0h_delete']
```

**`'m'` comes from `I'm`.** `\w+` splits on the apostrophe, `m` is not in the
stopword list, and step 2 turns it into the prefix term `"m"*` — which matches
**219,280 chunks, 45% of the index**. `"a"*` would match 72%. Eleven of the
24 benchmark queries contain a token of two characters or fewer (`m`, `in`,
`as`, `it`, `id`), and those eleven are exactly the slowest eleven queries.

**The cap takes the first 8 tokens, not the most informative 8.** Above,
`a0h_delete` — the only term that identifies anything, matching 3 chunks — is
the eighth. One more word earlier in the sentence and it would have been
dropped entirely. Document frequency is never consulted.

### 2. Build FTS queries

`fts5_queries()` emits up to two:

```python
strict   = " AND ".join(terms)   # all tokens must appear in one chunk
fallback = " OR ".join(terms)    # any token
```

Tokens containing `_` or consisting of digits become exact terms (`"a0h_delete"`);
everything else becomes a prefix term (`"access"*`).

**The strict pass returned zero rows for 24 of 24 benchmark queries.** Not
"rarely useful" — never useful, on any analyst-phrased question. Requiring eight
tokens to co-occur in a 333-character chunk is close to impossible, so in
practice retrieval is always the OR fallback. The strict pass is a latency cost
that returns nothing.

That is also where the latency tail comes from. Measured, whole benchmark:
median **398 ms**, p95 **2,083 ms**, max **7,061 ms**. A query with a junk
prefix term scans an enormous posting list in the fallback:

```
"m"*           219,280 chunks    82 ms just to count
"hyperspace"*      651 chunks     0.5 ms
"a0h_delete"         3 chunks     0.3 ms
```

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

Measured, Hit@5 by failure bucket:

| Bucket | Hit@5 | Why |
|---|---:|---|
| `named_table_lookup` | **1.000** | Hint fires; exact identifier match |
| `cross_table_synthesis` | 0.400 | Works when a table is named |
| `named_column_schema` | 0.200 | Column names are not unique across 40K docs |
| `business_concept_discovery` | **0.000** | No lexical overlap to match on |

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

These are cheap and measurable, and are deliberately **not** fixed yet: the
benchmark is the instrument, and you do not recalibrate the instrument and the
subject in the same change. Grow the benchmark first, then fix these and read
the delta.

1. **Drop tokens of ≤2 characters.** `"m"*` from `I'm` scans 45% of the index.
   Affects 11 of 24 queries and every slow one.
2. **Select the 8 most informative tokens, not the first 8.** Rank by document
   frequency so an identifier is never dropped for a filler word.
3. **Reconsider the strict AND pass.** It returns nothing on 24 of 24 queries
   while costing a full query round trip. Either require a subset of tokens or
   remove it.
4. **Validate the document hint.** Confirm the candidate matches a real
   `source_path` before letting it override ranking; `LINE` and `ABN` currently
   do not.
5. **Reconsider `MAX_CHUNKS_PER_DOCUMENT = 3` at `k=5`.** Three chunks of one
   wrong document is most of a top-5 budget.

Numbers here are comparable only within one `chunker_version`
(`index_contract.py`). Re-measure with `agent-harness-eval --suite retrieval`
after any change to chunk boundaries.
