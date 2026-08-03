# Synthetic Query Generation

Offline tooling that prepares Epic Clarity documentation for a future
domain-adapted semantic retriever. **None of it runs in the request path.**

The pipeline is deliberately split across three packages by what each stage is
for, not by when it was written:

| Stage | Module | Command |
|---|---|---|
| 1. Parse HTML into column chunks | `retrieval/column_parser.py` | `agent-harness-genq-parse` |
| 2. Assign leakage-safe splits | `evals/dataset_split.py` | `agent-harness-genq-split` |
| 3. Generate synthetic queries | `retrieval/genq/query_generation.py` | `agent-harness-genq-generate` |
| 4. Filter and review them | `evals/query_filter.py` | `agent-harness-genq-filter` |
| 5. MiniLM/FAISS baseline | `retrieval/genq/baseline_faiss.py` | `agent-harness-genq-baseline` |

Stage 1 is **also production code**: `retrieval/indexer.py` calls
`parse_column_records` to build the column chunks in the live SQLite index.
Changing it changes the production index — bump `CHUNKER_VERSION` in
`retrieval/index_contract.py` and rebuild.

## Table-level embedding input

Before embedding individual columns, the first dense baseline operates at table
level. `retrieval/metadata_extractor.py` reads the production SQLite index in
read-only mode and writes one deterministic record for every table with a
metadata chunk:

```bash
uv run agent-harness-metadata-extract var/rag/index-genq-columns-v5.sqlite \
  --output .local/embeddings/table-metadata/records.jsonl \
  --report .local/embeddings/table-metadata/extraction-report.json
```

Each encoder input is `Table: <name>\nDescription: <description>` when a
description exists, or `Table: <name>` as an explicit fallback. Records retain
the description status, document ID, metadata chunk ID, source and text hashes,
index version, and extractor version. Missing or duplicate metadata chunks fail
the run. This stage performs no model loading and creates no vector index.

### Collapsing shared descriptions

Epic reuses boilerplate across families of tables: 200 tables carry one identical
deprecation notice, 127 carry another, and 33 `*_DELETE_CT` tables share a single
sentence. Because the description is the only text that distinguishes one table
passage from another, those tables are not separable by any encoder — the
generator would pay for 200 requests to learn one fact, and contrastive training
would be asked to push apart passages that differ only in the table name.

`agent-harness-genq-metadata-convert --dedup-descriptions` keeps the first record
for each distinct description and drops the rest. Tables with no description are
never collapsed, because the `Table: <name>` fallback shares no text. The
extractor emits rows ordered by source path, so the surviving representative is
deterministic. Deduplication runs before `--limit`, so `--limit N` yields N
distinct passages rather than N raw rows.

Over the full 40,551-table corpus this drops 1,159 records across 425 duplicate
groups, leaving 25,394 chunks eligible for generation instead of 26,337. The
conversion report records `dedup_description_count` and `dedup_group_count`.

The first dense retrieval run consumes that artifact directly and evaluates it
against the same reviewed 50-query document benchmark as FTS:

```bash
uv run --group genq agent-harness-genq-baseline \
  .local/embeddings/table-metadata/records.jsonl \
  --benchmark-dir evals/retrieval/benchmark \
  --output-dir .local/embeddings/table-metadata/minilm-gold \
  --model sentence-transformers/all-MiniLM-L6-v2 \
  --batch-size 128 --top-k 10
```

This mode indexes all records by default and writes `tables.faiss`,
`table_mapping.jsonl`, `index_metadata.json`, and `gold_evaluation.json`. The
evaluation resolves portable catalog paths to extracted document IDs, reports
document MRR/Hit/Recall/nDCG at 1, 5, and 10 plus failure-bucket breakdowns, and
retains the ranked hits for inspection. Positive-only qrels do not produce
precision or nDCG; unsupported queries are diagnostic because this baseline has
no abstention threshold.

Stages 2 and 4 live in `evals/` because leakage-safe splits and query quality
gates are evaluation concerns whether or not a model is ever trained.

Generated artifacts are written below `.local/`, which is git-ignored because
its contents derive from proprietary Epic HTML.

```text
.local/
├── genq/corpus/     parsed chunks and leakage-safe splits
├── queries/         raw and reviewed synthetic queries
├── embeddings/      experimental FAISS indexes and evaluations
└── rag/index.sqlite production SQLite retrieval index
```

## Dependencies

Stages 1–4 need nothing beyond the core dependencies. Only stage 5 needs the
optional group:

```bash
uv sync --group genq
```

## Stage 1: Parse HTML into column chunks

One chunk per column definition, rather than several columns per passage. Other
chunk types are table metadata, primary keys, foreign keys, index information,
and relationships. Each chunk carries a readable logical `chunk_id`, a
source-file hash, and a text hash.

```bash
uv run agent-harness-genq-parse ../ClarityDictionaryHTML-full \
  --limit 100 \
  --output .local/genq/corpus/chunks.jsonl \
  --report .local/genq/corpus/parse_report.json
```

The 100-file proof of concept produced 1,613 chunks with no parser warnings.

## Stage 2: Leakage-safe splits

Whole source files are assigned to `train`, `validation`, or `test` by seeded
SHA-256 threshold — so runs are reproducible and adding files never moves an
existing file between splits. Ratios are approximately 80/10/10.

```bash
uv run agent-harness-genq-split .local/genq/corpus/chunks.jsonl \
  --output .local/genq/corpus/chunks_with_splits.jsonl \
  --report .local/genq/corpus/split_report.json \
  --seed epic-genq-v1
```

## Stage 3: Generate synthetic queries

Claude Haiku generates questions from stage 2 passages, one request per passage,
returning an exact count through a forced tool result. The system prompt asks
for questions a healthcare data analyst, report developer, or SQL developer
would ask — the domain fit is the whole point, since a generic web-search query
generator reintroduces exactly the distribution gap this pipeline exists to
close.

```bash
uv run agent-harness-genq-generate .local/genq/corpus/chunks_with_splits.jsonl \
  --model claude-haiku-4-5-20251001 \
  --output .local/queries/generated_queries.jsonl \
  --report .local/queries/generation_report.json \
  --queries-per-chunk 5 \
  --batch-size 8
```

Uses `ANTHROPIC_API_KEY` or `AI_HUB_API_KEY`, and honors `ANTHROPIC_BASE_URL`
and `ANTHROPIC_CUSTOM_HEADERS`. `--max-input-tokens` bounds the passage using
the four-characters-per-token approximation.

The API exposes no seeded sampling, so `--seed` records dataset provenance but
does not make output byte-reproducible. The generated JSONL is the artifact you
keep and version — it is written atomically with content hashes and stamps the
generator model onto every query, so the *dataset* is reproducible even though
the *generator* is not.

`QueryGenerator` is a Protocol, so a local generator can be added later as a
drop-in without touching the pipeline.

### Throughput and credentials

Generation is sequential: the provider loops passages inside a single
`generate` call, so `--batch-size` controls log granularity and nothing else.
At the observed ~1.6 s per request the 25,394-chunk deduplicated corpus takes
roughly eleven hours, and nothing is written until the final atomic write — an
interrupted run has to start over.

The Message Batches API would halve both cost and wall-clock, but the AI-hub
gateway in `ANTHROPIC_BASE_URL` does not proxy `/v1/messages/batches` (it
returns 404 while `/v1/messages` and `/v1/models` succeed). Until that endpoint
is proxied, or a direct Anthropic key is used, the sequential path is the only
one available.

`main` loads `.env` through `python-dotenv`, the same way `agent_host.config`
does, so credentials can live in one place; already-exported environment
variables still win. The gateway advertises `@`-versioned model IDs
(`claude-haiku-4-5@20251001`), so pass no `--model` flag and let
`DEFAULT_CLAUDE_MODEL` (`claude-haiku-4-5-20251001`) apply — that is the string
the completed 500-table run used.

## Stage 4: Filter and review

Joins every raw query back to its stage 2 chunk and applies deterministic
quality gates, recording one decision per query: `retain`, `reject`, or
`review`.

Rejected automatically: too few or too many words, excessive length,
source-filename mentions, model control tokens, long copies of the passage,
exact and near duplicates for the same chunk.

Sent to review: no meaningful lexical overlap with the source passage, or one
query pointing at multiple positives within a split. Low overlap is not treated
as proof of hallucination — a valid paraphrase may share few words.

The run fails if a query's chunk ID, source file, text hash, chunk type, split,
or split version disagrees with stage 2.

```bash
uv run agent-harness-genq-filter .local/queries/generated_queries.jsonl \
  --chunks .local/genq/corpus/chunks_with_splits.jsonl \
  --retained-output .local/queries/reviewed/retained_queries.jsonl \
  --review-output .local/queries/reviewed/query_reviews.jsonl \
  --report .local/queries/reviewed/filter_report.json
```

Deterministic filters remove mechanical defects. They do not establish
naturalness or factual support — inspect samples from every split and chunk type
before training on them.

## Stage 5: Pretrained MiniLM and exact FAISS baseline

Measures semantic retrieval without training anything. Embeds chunks and
retained queries with `sentence-transformers/all-MiniLM-L6-v2`, normalizes, and
stores passage vectors in `faiss.IndexFlatIP` — with normalized vectors, inner
product equals cosine similarity, and `IndexFlatIP` is exact, so
approximate-nearest-neighbor error cannot distort the baseline.

FAISS returns integer positions rather than application metadata, so the stage
writes a separate JSONL mapping each vector position to its stable table or
chunk identity. That mapping is required for correct retrieval.

The command supports two explicit evaluation modes: `--benchmark-dir` for the
table-level metadata artifact and reviewed gold queries shown above, or
`--queries` for the older synthetic chunk/query smoke evaluation below.

```bash
uv run --group genq agent-harness-genq-baseline .local/genq/corpus/chunks.jsonl \
  --queries .local/queries/reviewed/retained_queries.jsonl \
  --output-dir .local/embeddings/genq-baseline-smoke \
  --model sentence-transformers/all-MiniLM-L6-v2 \
  --limit 200 --batch-size 8 --top-k 10
```

It loads the MiniLM Transformer weights directly and applies the model's
standard attention-mask mean pooling, avoiding SentenceTransformers' optional
scikit-learn and SciPy imports at inference time.

Scoring uses the same ranking calculator as the main retrieval evaluator. Each
synthetic query currently has one labeled positive, so Recall@K equals Hit@K and
Precision@K can penalize semantically useful but unlabeled sibling chunks.

## Status

Stages 1–5 are implemented. The table-level MiniLM baseline over all 40,551
metadata records and the 50 reviewed queries produced document Hit@1 0.220,
Hit@5 0.268, Hit@10 0.366, and MRR 0.261 across the 41 answerable queries. This
is an honest pretrained dense floor, not a production retrieval target. Its
Hit@5 by answerable failure bucket was 0.500 for named table lookup, 0.300 for
named column schema, 0.300 for cross-table synthesis, and 0.000 for business
concept discovery. The nine unsupported queries remain diagnostics until an
abstention threshold is designed.

The earlier one-query, 200-chunk synthetic smoke test placed its positive at
rank 3 (Hit@5 1.000, MRR 0.333). That run validated the synthetic machinery but
established nothing about retrieval quality.

Synthetic-query evaluation does not replace the hand-authored analyst benchmark
in `evals/retrieval/benchmark/` — the two measure different things at different
granularity and their numbers are not comparable. A future training experiment
still needs a representative generation run across all chunk types and a manual
review pass.

## Tests

```bash
uv run pytest tests/retrieval/genq tests/test_dataset_split.py \
  tests/test_query_filter.py tests/retrieval/test_column_parser.py -q
```
