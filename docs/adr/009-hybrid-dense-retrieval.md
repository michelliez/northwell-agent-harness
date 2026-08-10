# ADR 009: Optional hybrid dense retrieval in the request path

## Status

Accepted (2026-08-10)

## Context

The frozen lexical retriever saturates on vocabulary mismatch: on the 85-query
reviewed benchmark, business-concept questions (no table or column name in the
query) hit the right document in the top 5 only 12% of the time, and document
recall@20 plateaued near 0.51 across four FTS configurations. A dense arm
(Qwen3-Embedding-0.6B over all 482K chunks, FAISS IndexFlatIP) reached 44% on
those concept queries but lost every identifier bucket — named-table lookups
fell from 1.000 to 0.667 because embeddings smear exact-name matches that FTS
resolves trivially.

Reciprocal-rank fusion of the two lists, measured in the evaluation repository
before any application change, was strictly non-regressive: every FTS
identifier bucket held exactly, business-concept tripled to 0.360, overall
hit@5 rose 0.541 → 0.622, and recall@20 rose to 0.649. Median latency was
~450 ms against ~14 ms for FTS alone.

## Decision

Port the measured fusion into `retrieval/` behind an optional configuration:

- `retrieval/dense.py` owns the FAISS artifact loader, the query-side
  Qwen3-Embedding encoder (last-token pooling, instruction-prefixed queries —
  the exact encoding the benchmark used), and `rrf_fuse`.
- `retrieve_documentation_context` fuses only when `DENSE_INDEX_DIR` is set;
  unset, the lexical path runs byte-identically to before.
- Fusion constants are frozen at the measured configuration (`RRF_K = 60`,
  candidate depth `top_k * 2`). Changing them requires a benchmark rerun.
- Dependencies (torch, transformers, faiss, numpy) live in the optional
  `dense` group, imported lazily, failing closed with an actionable message —
  the same shape as the BigQuery adapter.
- The searcher and encoder load once per process and stay resident.

Fail-closed choices: a dense hit whose chunk ID is absent from the SQLite
index aborts retrieval rather than silently degrading, because it proves the
artifact was built against a different index. Artifacts built with a
non-Qwen3-Embedding encoder are refused rather than pooled incorrectly —
wrong pooling produces embeddings that look valid and retrieve poorly, the
worst available failure mode.

The exploration tool loop (`find_table_doc`, `get_doc_section`,
`search_columns`) stays FTS-only: its queries are identifier-shaped, which is
the bucket lexical search already wins, and per-tool-call encoder latency
would multiply inside the loop. Only the retrieval node path and its
exploration fallback fuse.

## Consequences

- Concept-phrased questions — the first thing a demo audience asks — retrieve
  usable evidence three times as often, with zero regression on named lookups.
- A configured request path costs ~450 ms median added latency and holds a
  ~1.2 GB encoder resident (GPU when available). Deployments that cannot pay
  this leave `DENSE_INDEX_DIR` unset and keep today's behavior.
- The FAISS artifact is a build product tied to one index; index rebuilds at a
  new chunker version require re-embedding the corpus (~2.5 h on a consumer
  GPU).
