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

## Reviewed failure buckets

The tracked benchmark uses analyst-written questions rather than phrases copied
from HTML descriptions. Its 24 queries cover five retrieval failure buckets:

- `named_table_lookup`: a known table must yield its purpose or grain;
- `named_column_schema`: known fields must yield types, meanings, or key roles;
- `business_concept_discovery`: business language must find the right tables;
- `cross_table_synthesis`: evidence must be compared across documents;
- `negative_unsupported`: the corpus does not support the requested answer.

The query file records `failure_bucket`, and reports include document- and
chunk-level metrics for each bucket. The 70 chunk qrels are manually reviewed
against the column-level v5 index; do not regenerate them from source
descriptions or mechanically patch old heading paths.

## Comparability

The qrels are **portable**: they name documents semantically
(`epic_clarity:<object_type>:<OBJECT_NAME>`) and are resolved to the active
index's runtime chunk and document IDs at evaluation time. The same benchmark
therefore runs against any index built from the same corpus.

Every report records `index_version` and `chunker_version`, read from the index
being evaluated rather than from the current source. **Numbers are only
comparable within one `chunker_version`** — changing chunk boundaries changes
what counts as a relevant chunk. `CHUNKER_VERSION` is currently
`section-table-genq-columns-v5` (`src/retrieval/index_contract.py`) and must be
incremented whenever chunk boundaries change.

Reports also carry `judgment_complete`, `unjudged_document_total`, and
`unjudged_chunk_total`. Treat recall as a lower bound whenever judgment is
incomplete.

## Synthetic query generation

Synthetic queries come from the GenQ pipeline: parse the HTML corpus, split it,
generate queries with Claude, filter them, then score retrieval with a FAISS
baseline. It spans three packages by concern — parsing in `retrieval/`,
splitting and filtering in `evals/`, generation and the baseline in
`retrieval/genq/`. See [that README](../../src/retrieval/genq/README.md).

```powershell
uv run agent-harness-genq-parse <HTML_PATH>
uv run agent-harness-genq-split
uv run agent-harness-genq-generate
uv run agent-harness-genq-filter
uv run --group genq agent-harness-genq-baseline
```

Only the FAISS baseline needs the optional `genq` dependency group:

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
