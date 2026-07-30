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
`parse_column_records` to build the column chunks in the live SQLite index, so
the same passage text and `chunk_id` serve lexical retrieval and any future
semantic retrieval. Changing it changes the production index — bump
`CHUNKER_VERSION` in `retrieval/index_contract.py` and rebuild.

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
writes a separate JSONL mapping each vector position to its stable `chunk_id`.
That mapping is required for correct retrieval.

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

Stages 1–5 are implemented. The only baseline result so far is a one-query,
200-chunk smoke test: positive at rank 3, Hit@5 1.000, MRR 0.333. The top three
hits were the metadata chunks for `A0H_MAP`, `A0H_UPDATE`, and the known
positive `A0H_DELETE` — closely related documents, so a single-positive
synthetic label likely understates semantic relevance. That validates the
machinery and establishes nothing about quality.

Before this pipeline can inform a real decision it needs a representative
generation run across all chunk types, a manual review pass, and a baseline over
a materially larger query set. Synthetic-query evaluation also does not replace
the hand-authored analyst benchmark in `evals/retrieval/benchmark/` — the two
measure different things at different granularity and their numbers are not
comparable.

## Tests

```bash
uv run pytest tests/retrieval/genq tests/test_dataset_split.py \
  tests/test_query_filter.py tests/retrieval/test_column_parser.py -q
```
