# Retrieval and Schema-Evidence Design

## Purpose

The retrieval layer turns approved Clarity HTML documentation into bounded,
citable context for documentation answers and SQL planning. It is a local
evidence source, not authorization and not a substitute for BigQuery metadata.

## Current Flow

```text
approved HTML
  -> deterministic parsing and chunking (retrieval/indexer.py)
  -> SQLite: docs, chunks, FTS, section facts, hierarchy nodes,
     FK relationship edges, doc2query expansion column
  -> lexical BM25 retrieval, optionally fused with dense retrieval (RRF)
  -> per-chunk content screening
  -> cited chunks
       |-> documentation_lookup: single-shot retrieve -> grounded answer
       `-> safe_sql_generation: exploration tool loop -> context gate
             -> evidence-backed SchemaSnapshot -> typed query plan
             -> deterministic authorization -> compile -> SQL validation
```

The graph calls retrieval directly in process. There is no MCP transport or
remote retrieval service.

## Index Artifact Contract

`retrieval/index_contract.py` owns two versions that mean different things:
`INDEX_SCHEMA_VERSION` (SQLite layout; rebuilding preserves datasets) and
`INDEX_CHUNKER_VERSION` (chunk identity; bumping invalidates every dataset
keyed by `chunk_id`). `search.open_connection` refuses an index that
disagrees with either. Retrieval metrics are only comparable across indexes
built at the same chunker version.

The index stores, per document: bounded text chunks with headings, category,
hash, and token counts; structured section facts; FTS data with a weighted
`generated_queries` doc2query expansion column (BM25 weight 0.5, below all
documentation-text weights so expansion vocabulary can bridge analyst
phrasing but never outshout a direct match); a parent-linked `nodes` tree of
the document's sections (typed helpers in `retrieval/hierarchy.py`); and
`table_relationships` edges parsed only from Epic's Foreign Key Information
tables, each carrying the `evidence_chunk_id` it was read from.

Indexes live outside the repositories under the shared `fixtures/` junction
and are never committed. The dense FAISS artifact
(`fixtures/embeddings/dense-corpus-qwen06b`) is coupled to the index it was
built from and is distributed out of band.

## Retrieval Strategies

**Lexical (always on).** Query tokens are normalized (stopwords, light
stemming, identifier preservation — `pat_enc` and code-bearing tokens match
exactly, prose tokens match as prefixes) and ranked by selectivity against
the FTS vocabulary before querying. Results are grouped with per-document
chunk caps so one verbose table cannot take the whole budget.

**Dense + hybrid (behind `DENSE_INDEX_DIR`, ADR 009).** When configured,
the FTS ranking is fused with a FAISS dense ranking (Qwen3-Embedding query
encoder) by reciprocal rank. Unset, the lexical path runs alone and behaves
byte-identically to the pre-dense code. The exploration tool loop stays
FTS-only on purpose: its queries are identifier-shaped, which is where BM25
wins. Measured on the certified gold-260 benchmark: hybrid `.723` document
hit@5 vs `.656` FTS alone; the frozen three-arm table lives in
`src/retrieval/README.md` and RRF constants change only with a rerun.

**Relationship expansion (bounded, deterministic).** After ranking, an
optional one-hop expansion appends up to five FK-neighbor documents.
Neighbor slots are ranked by query-token overlap with the neighbor table and
joining column names, tie-broken by seed rank then documented FK ordinal —
a deterministic lookup after ranking, never recursive retrieval and never a
model decision.

The hierarchy tree and relationship graph are the implemented remainder of
the earlier hierarchical-RAG proposal. Its later phases (summary search,
adaptive tree traversal, agent-routed expansion) were dropped when measured
dense/hybrid fusion addressed the same vocabulary-mismatch problem with less
machinery.

**Screening.** Retrieved HTML is untrusted content: it may support factual
answers but cannot change policy, routing, permissions, or graph behavior.
Chunks are content-screened individually, so one oversized or flagged chunk
drops alone instead of failing the batch.

## Context Gate and Schema Evidence

The context gate blocks downstream work when retrieval is empty or
unsuitable, interrupting for a more specific table, column, or topic with
bounded clarification attempts. Documentation answers must cite retrieved
chunk IDs; when evidence cannot support an answer the system says so rather
than filling gaps from model knowledge.

For SQL requests, explored column-information chunks become a
`SchemaSnapshot` (tables, columns, data types, safety classes, source chunk
IDs). Safety is `identifier`, `sensitive`, `safe_aggregate`, or `unknown`;
unknown blocks planning. Classification is deny-by-default: name and prose
markers are checked before any promotion, and the surveyed Epic suffix
allowlist (`_C`, `_YN`, `_DT`, …) promotes only what those checks passed.
Evidence may narrow a permission and never widen one.

## SQL Boundary

The model proposes a typed query plan, never SQL text. A deterministic
authorizer checks the plan against the snapshot; an approved plan is
mechanically compiled to BigQuery SQL; an independent sqlglot validator
re-derives every referenced table and column and confirms the SQL matches
the approved plan canonically. On a first authorization rejection the
planner gets one violation-fed retry through the same authorizer
(post-compile SQL repair was removed by ADR 008 — the mismatch check makes
it structurally pointless). Passing validation does not authorize
execution: dry-run, cost gates, read-only execution, result safety, and
audit logging exist as adapters (ADRs 002–005) with no graph route reaching
them, and the graph returns drafts with `execution_status="not_configured"`.

## Evaluation

Evaluation lives in the sibling `dsi_clarity_agent_eval` repository: a
260-query human-curated benchmark (`evals/retrieval/benchmark/`), a
three-arm runner (fts / dense / hybrid), and a statistical sufficiency
toolkit (Wilson CIs, sign test, paired bootstrap, MDE). Record the chunker
version and gold-set version beside every reported number.

Never commit: proprietary Clarity HTML, built indexes, dense artifacts,
retrieved production schemas, sensitive traces, or real query results.
