# Retrieval Evaluation Data

This directory separates the reviewed retrieval benchmark from generated query
data.

- `benchmark/` is tracked. It holds the small, hand-authored query, document
  qrel, chunk qrel, and document catalog files used by
  `agent-harness-eval --suite retrieval`.
- `generated/` is git-ignored. It holds local query-generation artifacts whose
  records reproduce descriptions from the proprietary Clarity corpus and must
  never be committed.

## Running the benchmark

```powershell
uv run agent-harness-eval --suite retrieval --k 5 10
```

The index defaults to `.local/rag/index.sqlite`. Point at another local index
with `--db`, and at another benchmark directory with `--benchmark-dir`.

Validate the qrels without running retrieval:

```powershell
uv run python -m evals.retrieval_gold
```

Reports are written to `.local/evals/retrieval-evaluation-<timestamp>.json`.

## Comparability

The qrels are **portable**: they name documents semantically
(`epic_clarity:<object_type>:<OBJECT_NAME>`) and are resolved to the active
index's runtime chunk and document IDs at evaluation time. The same benchmark
therefore runs against any index built from the same corpus.

Every report records `index_version` and `chunker_version`, read from the index
being evaluated rather than from the current source. **Numbers are only
comparable within one `chunker_version`** — changing chunk boundaries changes
what counts as a relevant chunk. `CHUNKER_VERSION` is currently
`section-table-v3` (`src/retrieval/indexer.py`) and must be incremented whenever
chunk boundaries change.

Reports also carry `judgment_complete`, `unjudged_document_total`, and
`unjudged_chunk_total`. Treat recall as a lower bound whenever judgment is
incomplete.

## Generated query data

The `generated/` layout is:

```text
generated/
  documents.jsonl
  training_queries.jsonl
  development_queries.jsonl
  evaluation_queries.jsonl
```

`documents.jsonl` is partitioned *before* queries are generated, so the three
query files have disjoint positive-document sets and held-out evaluation
documents cannot also become training pairs.

Generate them from the approved HTML corpus:

```powershell
uv run agent-harness-generate-retrieval-queries <HTML_PATH>
```

This uses Anthropic to turn each page's table description into queries in three
styles. Pass `--generator local` for deterministic templates and no API calls.

Evaluate a split:

```powershell
uv run agent-harness-eval --suite retrieval --generated-split development --k 5 10
```

Use the development split while tuning retrieval. Run the evaluation split only
for a final held-out measurement. Add `--limit 100` for a quick smoke test, or
`--sample 1000 --sample-seed manager-review-v1` for a reproducible subset —
sampling is proportional within each query style, so a sample stays
representative rather than being the first N rows.

### Two generators, do not confuse them

| Script | Method | Purpose |
| --- | --- | --- |
| `evals/retrieval_query_generation.py` | Anthropic, from HTML descriptions | Produces `generated/` |
| `retrieval/genq/query_generation.py` | GenQ / T5, from indexed chunks | Separate experiment, optional deps |

Both are offline tools. Neither may enter the request path.
