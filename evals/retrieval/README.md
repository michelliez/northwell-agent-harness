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

The index defaults to `RAG_DB_PATH` when set, then `.local/rag/index.sqlite`.
Override either value with `--db`, and point at another benchmark directory
with `--benchmark-dir`.

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

## Synthetic query generation

Synthetic queries are produced by the GenQ pipeline under `src/retrieval/genq/`,
which is a complete stack of its own: parse the HTML corpus, split it, generate
queries, filter them, then score retrieval with a FAISS baseline.

```powershell
uv run agent-harness-genq-parse <HTML_PATH>
uv run agent-harness-genq-split
uv run agent-harness-genq-generate --provider claude
uv run agent-harness-genq-filter
uv run agent-harness-genq-baseline
```

`--provider claude` generates with Claude; `--provider t5` uses the local T5
model. Both need the optional `genq` dependency group:

```powershell
uv sync --group genq
```

GenQ splits the corpus before generating, so held-out evaluation chunks cannot
also become training pairs. It is an offline tool and must never enter the
request path.

The reviewed benchmark above and the GenQ pipeline measure different things at
different granularity: the benchmark is a small hand-authored document-level
qrel set, GenQ is a large synthetic chunk-level set. Do not compare their
numbers to each other.
