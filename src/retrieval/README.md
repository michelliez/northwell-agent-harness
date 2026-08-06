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

`search_tokens()` splits on `\w+`, lowercases, drops the stopword list, strips a
trailing `s` from words over four characters, removes terms that cannot match
the index, dedupes, and keeps the **8 most selective** survivors
(`MAX_FTS_QUERY_TOKENS`).

Both rules exist because of measured failures, not taste.

**Short tokens must not become prefixes.** `\w+` splits `I'm` on the apostrophe
and leaves `m`; `"m"*` spans 511,742 chunk-term rows against 651 for
`"hyperspace"*`. Tokens under three characters are therefore dropped except for
a small domain set (`DX`, `ED`, `ER`, `IP`, `OR`, `RX`), which is matched exactly.
Digit-bearing tokens survive normalization but are also matched exactly.

**Rarity beats position.** Truncating to the first 8 tokens ranks by where a
word sits in the sentence, which is unrelated to how much it narrows the search.
`select_query_tokens()` ranks by estimated FTS match volume instead. A schema
identifier contains `_` or mixes letters and digits; it receives priority only
after the index confirms that its exact phrase matches at least one row. Numeric
literals are exact terms but are not automatically treated as identifiers.

`match_count_lookup()` uses real MATCH row counts for exact terms and identifier
phrases. Prefix terms use the sum of the FTS vocabulary posting counts across
the prefix range, via an `fts5vocab` table created in `temp` so the main index
stays read-only. That sum is a fast estimate, not an exact distinct-row count: a
chunk containing both `admission` and `admissions` contributes to both posting
lists. Without a provider the function falls back to positional truncation.

Zero-count terms are removed. An OR clause that cannot match cannot improve
recall, and treating zero as "rarest" allowed unseen jargon or misspellings to
consume the eight-token budget and displace terms that could retrieve rows.

On a real benchmark query the difference is not subtle:

```
"I'm reviewing the incremental cleanup feed for Hyperspace access.
 What does A0H_DELETE contain, and what field identifies each row?"

by position:  m, reviewing, incremental, cleanup, feed, hyperspace,
              access, a0h_delete
by rarity:    a0h_delete(3), reviewing(64), hyperspace(651), cleanup(698),
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

Schema identifiers, numeric literals, and the short domain terms become exact
terms (`"a0h_delete"`, `"r1"`, `"ed"`); ordinary words become prefix terms
(`"access"*`). `fts5_term()` owns this classification so selection and query
construction cannot disagree about a token's match mode.

A conjunctive pass used to run first, and its rows were preferred on the theory
that a chunk containing every term beats one containing any term. **The strict
pass returned zero rows for all 50 gold benchmark queries.** That establishes
that it was dead for this workload, not that conjunction can never match some
future short query. It was removed; the OR ranking already rewards chunks that
match multiple terms through BM25.

Be precise about what removing it bought. Measured across the benchmark, the
strict pass cost **13 ms per query** against the disjunction's **133 ms** — it
was dead weight for the measured workload, not the latency tail.

Subset conjunction is deliberately not pursued in this baseline. It is another
ranking parameter to tune on the same small gold set, while dense retrieval is
the component intended to address the measured semantic-recall deficit.

The latency tail comes from step 1, not from here: a junk prefix term scans an
enormous posting list, and dropping those terms moved the whole distribution.

```
                    original   tokenizer   no AND pass   frozen
median                599 ms      228 ms        199 ms    209 ms
p95                 2,334 ms    1,614 ms      1,507 ms  1,518 ms
```

The frozen pass checks every surviving token for index evidence, including
queries below the eight-token cap. Across all 50 queries those checks took about
0.83 seconds total in a direct live-index probe. The warm end-to-end run added
10 ms at the median and 11 ms at p95 versus the previous pass.

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

### Optional relationship expansion

Version 5 indexes the explicit rows under each document's **Foreign Key
Information** section in `table_relationships`. Each edge retains its source
and destination columns and the chunk containing the source evidence. After
all documents are indexed, destination table names are resolved to document
IDs within that same index; unresolved names are retained but cannot be used
for expansion.

Call `retrieve_documentation_context(..., include_relationships=True)` (or the
equivalent option on `retrieval.client.retrieve_documentation`) to expand the
BM25 seed documents by one bounded hop. The result's `relationships` list
records direction, related document, join columns, and `evidence_chunk_id`.
Unrelated documents are never inferred from semantic similarity. This option
is currently off by default and is not yet connected to SQL planning.

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

## Frozen lexical baseline

The lexical ranker is frozen after the evidence-aware token-selection pass.
Further work should compare dense and hybrid retrieval rather than tune more FTS
parameters against these same 50 gold queries.

Two behaviors remain visible but move to hybrid evaluation:

1. Invalid document hints such as `LINE` and `ABN` match no live document, so
   the current SQL assigns every result the same `document_rank`; existence
   validation alone would not change these rankings. A future hint feature needs
   confidence semantics, not merely a catalog lookup.
2. `MAX_CHUNKS_PER_DOCUMENT = 3` can let one wrong document consume most of a
   top-5 budget. The best cap depends on the lexical/dense fusion candidate set,
   so it should be selected with the hybrid postprocessor rather than here.

Fixing the tokenizer moved document Hit@5 from 0.488 to 0.512 and recall@5 from
0.427 to 0.463 while holding `named_table_lookup` at 1.000. Document MRR fell
0.476 → 0.462: a wider token set surfaces more correct documents but not always
at the same rank. Hit and recall rising while MRR dips is the expected shape of
a recall change, and it is the trade this system wants — a document that was
absent is now retrievable.

Removing the strict AND pass changed all 50 complete chunk rankings by exactly
zero positions. That is stronger evidence than rounded aggregate metrics that
the branch never contributed to this workload.

The final evidence-aware pass preserved every aggregate document and chunk
quality metric exactly through `k=10`. Two rankings changed: Q18 dropped the
zero-hit word `joinable`, and unsupported Q47 dropped another zero-hit term.
Neither change affected a scored metric.

Numbers here are comparable only within one `chunker_version`
(`index_contract.py`). Re-measure with `agent-harness-eval --suite retrieval`
after any change to chunk boundaries.
