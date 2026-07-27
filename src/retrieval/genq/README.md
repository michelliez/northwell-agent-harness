# GenQ Semantic Retrieval Pipeline

This package prepares Epic Clarity HTML documentation for domain-adapted
semantic retrieval.

The first five stages are implemented:

1. Parse Epic HTML into structured chunks.
2. Assign leakage-safe train, validation, and test splits.
3. Generate raw synthetic queries with the BEIR T5 query generator.
4. Filter raw queries and record an auditable quality decision.
5. Build and evaluate an exact pretrained-model FAISS baseline.

Generated artifacts are written below `var/genq/`. That directory is ignored by
Git because its files contain information derived from proprietary Epic HTML.

Run all commands from the `agent-eval-harness-spike` project directory.

## Environment setup

Install the base, development, and GenQ dependencies:

```bash
uv sync --all-groups
```

The `genq` dependency group includes Transformers, PyTorch, SentencePiece,
SentenceTransformers, Datasets, and FAISS.

## Stage 1: Parse HTML into canonical chunks

### Purpose

Stage 1 converts Epic HTML into validated, self-contained retrieval passages.
The important design choice is one chunk per column definition rather than
combining several columns into one passage.

Other chunk types include:

- Table metadata
- Primary keys
- Foreign keys
- Index information
- Relationships

Each chunk has a readable logical `chunk_id`, a source-file hash, a text hash,
and enough metadata to trace it back to the original HTML.

### Input

```text
../ClarityDictionaryHTML-full/*.html
```

### Outputs

```text
var/genq/stage1/chunks.jsonl
var/genq/stage1/parse_report.json
```

### Proof-of-concept command

```bash
uv run --all-groups agent-harness-genq-parse \
  ../ClarityDictionaryHTML-full \
  --limit 100 \
  --output var/genq/stage1/chunks.jsonl \
  --report var/genq/stage1/parse_report.json
```

The current 100-file proof of concept produced 1,613 chunks with no parser
warnings.

## Stage 2: Create leakage-safe dataset splits

### Purpose

Stage 2 assigns complete source files to `train`, `validation`, or `test`.
Every chunk from one HTML file receives the same split.

Assignment uses seeded SHA-256 thresholds. This has two useful properties:

- Repeated runs produce the same assignments.
- Adding new source files does not move existing files between splits.

The ratios are approximately 80/10/10. Exact counts are not guaranteed for a
small sample.

### Input

```text
var/genq/stage1/chunks.jsonl
```

### Outputs

```text
var/genq/stage2/chunks_with_splits.jsonl
var/genq/stage2/split_report.json
```

### Command

```bash
uv run --all-groups agent-harness-genq-split \
  var/genq/stage1/chunks.jsonl \
  --output var/genq/stage2/chunks_with_splits.jsonl \
  --report var/genq/stage2/split_report.json \
  --seed epic-genq-v1
```

The current proof of concept assigned:

| Split | Source files | Chunks |
|---|---:|---:|
| Train | 82 | 1,432 |
| Validation | 6 | 61 |
| Test | 12 | 120 |

The report verifies that there are no duplicate chunk IDs or source files that
leak across splits.

## Stage 3: Generate raw synthetic queries

### Purpose

Stage 3 uses `BeIR/query-gen-msmarco-t5-large-v1` to generate questions from
the Stage 2 passages.

Defaults follow the SentenceTransformers GenQ example while using the modern
tokenizer call interface:

- Five queries per passage
- Batch size eight
- Maximum 300 input tokens
- Maximum 64 new query tokens
- Nucleus sampling with `top_p=0.95`
- Passages shorter than 100 characters skipped

Every generated query inherits the source chunk's split and stores its known
positive `relevant_chunk_id`.

### Input

```text
var/genq/stage2/chunks_with_splits.jsonl
```

### Smoke-test outputs

```text
var/genq/stage3/generated_queries.smoke.jsonl
var/genq/stage3/generation_report.smoke.json
```

### Bounded smoke-test command

```bash
uv run --all-groups agent-harness-genq-generate \
  var/genq/stage2/chunks_with_splits.jsonl \
  --output var/genq/stage3/generated_queries.smoke.jsonl \
  --report var/genq/stage3/generation_report.smoke.json \
  --limit 1 \
  --queries-per-chunk 2 \
  --batch-size 1 \
  --device cpu
```

### Full 100-file proof-of-concept command

```bash
uv run --all-groups agent-harness-genq-generate \
  var/genq/stage2/chunks_with_splits.jsonl \
  --output var/genq/stage3/generated_queries.jsonl \
  --report var/genq/stage3/generation_report.json \
  --queries-per-chunk 5 \
  --batch-size 8 \
  --device auto
```

The full command has not been run yet. The current corpus contains 1,585
passages that meet the 100-character threshold, so the default configuration
would generate approximately 7,925 raw queries. The large T5 model may take
hours on CPU. Test a representative batch and confirm CUDA or Apple MPS
behavior before starting the complete run.

## Stage 4: Filter and review raw queries

### Purpose

Stage 4 joins every raw query back to its Stage 2 source chunk and applies
conservative deterministic quality gates.

Every query receives one decision:

- `retain`: passed the deterministic gates
- `reject`: contains a definite mechanical defect
- `review`: requires human judgment

Automatic rejection covers:

- Too few or too many words
- Excessive character length
- Source-filename mentions
- Model control-token artifacts
- Long copies of the source passage
- Exact duplicates for the same chunk
- Near duplicates for the same chunk

Queries are sent to review when:

- They have no meaningful lexical overlap with the source passage.
- The same query points to multiple positive chunks within one split.

Low lexical overlap is not treated as proof of hallucination because a valid
semantic paraphrase may use different words.

The run fails if the query's chunk ID, source file, text hash, chunk type,
split, or split version disagrees with Stage 2.

### Smoke-test inputs

```text
var/genq/stage3/generated_queries.smoke.jsonl
var/genq/stage2/chunks_with_splits.jsonl
```

### Smoke-test outputs

```text
var/genq/stage4/retained_queries.smoke.jsonl
var/genq/stage4/query_reviews.smoke.jsonl
var/genq/stage4/filter_report.smoke.json
```

### Smoke-test command

```bash
uv run --all-groups agent-harness-genq-filter \
  var/genq/stage3/generated_queries.smoke.jsonl \
  --chunks var/genq/stage2/chunks_with_splits.jsonl \
  --retained-output var/genq/stage4/retained_queries.smoke.jsonl \
  --review-output var/genq/stage4/query_reviews.smoke.jsonl \
  --report var/genq/stage4/filter_report.smoke.json
```

The real-model smoke test produced two raw queries. Stage 4 retained one and
rejected one for having fewer than three words.

### Full proof-of-concept command

Run this after the full Stage 3 artifact exists:

```bash
uv run --all-groups agent-harness-genq-filter \
  var/genq/stage3/generated_queries.jsonl \
  --chunks var/genq/stage2/chunks_with_splits.jsonl \
  --retained-output var/genq/stage4/retained_queries.jsonl \
  --review-output var/genq/stage4/query_reviews.jsonl \
  --report var/genq/stage4/filter_report.json
```

Deterministic filters remove obvious defects but do not guarantee naturalness
or factual support. Manually inspect retained and review samples from every
split and chunk type before training.

## Stage 5: Pretrained MiniLM and exact FAISS baseline

### Purpose

Stage 5 measures semantic retrieval without training a model. It embeds
canonical chunks and retained questions using
`sentence-transformers/all-MiniLM-L6-v2`, normalizes the embeddings, and stores
the passage vectors in `faiss.IndexFlatIP`.

With normalized vectors, inner product is equivalent to cosine similarity.
`IndexFlatIP` performs exact search, so approximate-nearest-neighbor error
cannot distort the baseline.

The stage writes a separate JSONL mapping from each FAISS vector position to
its stable `chunk_id`. FAISS returns integer positions rather than application
metadata, so this mapping is required for correct retrieval.

For the MacBook MPS smoke test, the baseline is deliberately limited to the
first 200 canonical chunks. The retained smoke query's known positive chunk is
inside this subset.

### Inputs

```text
var/genq/stage1/chunks.jsonl
var/genq/stage4/retained_queries.smoke.jsonl
```

### Outputs

```text
var/genq/baseline-smoke/corpus.faiss
var/genq/baseline-smoke/chunk_mapping.jsonl
var/genq/baseline-smoke/index_metadata.json
var/genq/baseline-smoke/smoke_evaluation.json
```

### MPS smoke-test command

```bash
uv run --all-groups agent-harness-genq-baseline \
  var/genq/stage1/chunks.jsonl \
  --queries var/genq/stage4/retained_queries.smoke.jsonl \
  --output-dir var/genq/baseline-smoke \
  --model sentence-transformers/all-MiniLM-L6-v2 \
  --device mps \
  --limit 200 \
  --batch-size 8 \
  --top-k 10
```

The one-query smoke result was:

```text
positive rank: 3
Precision@1:  0.000
Precision@5:  0.200
Precision@10: 0.100
Recall@1:     0.000
Recall@5:     1.000
Recall@10:    1.000
Hit@1:        0.000
Hit@5:        1.000
Hit@10:       1.000
MRR:          0.333
nDCG@10:      0.500
```

The top three results were the metadata chunks for `A0H_MAP`, `A0H_UPDATE`,
and the known positive `A0H_DELETE`. These documents are closely related, so
the single-positive synthetic label may understate semantic relevance. One
query validates the machinery but cannot establish model quality.

Stage 5 uses the same shared ranking calculator as the main retrieval
evaluator. Because each synthetic query currently has one labeled positive,
Recall@K is equivalent to Hit@K and Precision@K can count semantically useful
but unlabeled sibling chunks as false positives.

The implementation loads the MiniLM Transformer weights directly and applies
the model's standard attention-mask mean pooling. This avoids importing
SentenceTransformers' optional scikit-learn and SciPy utilities during
baseline inference. SentenceTransformers remains installed for later training.

## Tests and quality checks

Run the focused GenQ tests:

```bash
uv run --all-groups pytest tests/retrieval/genq -q
```

Run linting and type checking:

```bash
uv run --all-groups ruff check src/retrieval/genq tests/retrieval/genq
uv run --all-groups pyright src/retrieval/genq
```

Run the complete repository suite:

```bash
uv run --all-groups pytest -q
```

At the end of the Stage 5 focused implementation:

- 61 focused GenQ tests passed.
- Ruff passed.
- Pyright reported no errors or warnings.

## Next steps

### 1. Scale and review query generation

Before training:

1. Generate a representative sample covering column definitions, table
   metadata, primary keys, foreign keys, indexes, and relationships.
2. Test an appropriate device and batch size.
3. Run Stage 3 for the complete 100-file proof of concept.
4. Run Stage 4 over the complete raw-query artifact.
5. Manually inspect retained and review samples from every split and chunk
   type.

### 2. Convert retained queries into training pairs

Join each retained query to its exact positive passage:

```json
{
  "query_id": "q_123",
  "anchor": "What identifies an updated access record?",
  "positive": "Table A0H_UPDATE. Column ID...",
  "relevant_chunk_id": "A0H_UPDATE__COLUMN_DEFINITION__ID",
  "split": "train"
}
```

Write separate train, validation, and test artifacts and verify that every
query resolves to exactly one current passage.

### 3. Expand the unfine-tuned baseline evaluation

Rerun the implemented MiniLM/FAISS baseline over the full proof-of-concept
corpus and a materially larger retained query set. Record Hit@1, Hit@5,
Hit@10, Precision@K, Recall@K, MRR, and nDCG where appropriate.

The one-query, 200-chunk smoke result validates the pipeline but is not a
meaningful quality estimate.

### 4. Fine-tune the SentenceTransformer bi-encoder

Train with:

- Query-positive-passage pairs
- `MultipleNegativesRankingLoss`
- `BatchSamplers.NO_DUPLICATES`
- Train data only
- Validation data for development feedback

Avoid duplicate and near-duplicate passages in the same batch where feasible
to reduce false negatives.

### 5. Save and validate the trained model

Store the model, tokenizer, base-model identity, training configuration,
dataset hashes, and training metrics. Verify that the saved model reloads and
produces consistent embeddings.

### 6. Compare the trained model with the baseline

Evaluate both models on the held-out validation and test queries using exact
cosine-similarity ranking. Do not use the test set for training or parameter
selection.

### 7. Embed the canonical Epic corpus

Use the winning model to embed every canonical chunk. Normalize the vectors
and preserve the exact relationship between vector positions and stable
`chunk_id` values.

### 8. Build the FAISS index

Start with normalized vectors and `IndexFlatIP`. This provides exact cosine
similarity without approximate-nearest-neighbor error.

### 9. Implement runtime semantic retrieval

Embed a new user question, search FAISS, map vector positions back to chunk
IDs, and return the corresponding text and metadata.

This pipeline concerns semantic document retrieval, not SQL generation or
runtime query rewriting.

### 10. Compare or integrate SQLite retrieval

Later, make the canonical Stage 1 JSONL corpus available to SQLite FTS so
lexical and semantic retrieval use the same passages and stable IDs. Compare
FTS-only, vector-only, and potential hybrid retrieval.

### 11. Scale to the complete Epic corpus

Scale with inspection checkpoints:

```text
100 files -> 1,000 files -> 10,000 files -> 40,551 files
```

At every checkpoint, inspect parsing warnings, query quality, generation time,
memory use, index size, and retrieval metrics.

### 12. Evaluate real analyst questions

Create a manually labeled dataset of real analyst questions with one or more
relevant chunks. Synthetic-query evaluation is useful, but it is not a
substitute for human-written evaluation queries.
