# Retrieval Implementation Plan

This file tracks the RAG work that is incomplete or missing. The current system
has a working SQLite FTS retrieval path, but it still needs evaluation,
grounding checks, semantic retrieval, and production-hardening.

## Current Baseline

Implemented:

- HTML indexing with BeautifulSoup in `indexer.py`.
- Stable `doc_id` and `chunk_id` generation.
- SQLite tables for `docs`, `chunks`, `chunks_fts`, `section_facts`, and
  `index_metadata`.
- FTS5 keyword retrieval through `mcp_server.py`.
- MCP tools for searching and fetching documentation chunks.
- Agent-side retrieval client in `client.py`.
- `documentation_lookup` workflow that retrieves chunks and asks the model to
  answer from approved context.
- Basic index validation and audit support.

Not yet implemented:

- Retrieval recall evaluation.
- Citation validation.
- Semantic embeddings.
- Vector search.
- Hybrid ranking.
- Prompt-injection scanning for retrieved chunks.
- Robust runtime configuration for the RAG database path.

## Priority 1: Retrieval Evaluation

Goal:

Measure whether retrieval returns the right document or chunk.

Implement:

- Add a small golden eval set:
  - `query`
  - `expected_source_path`
  - optional `expected_heading_contains`
  - optional `forbidden_source_path`
- Add a retrieval eval runner that calls the RAG MCP tools or the local search
  functions.
- Report:
  - `Recall@1`
  - `Recall@5`
  - `Recall@10`
  - `MRR`
  - missing expected documents

Definition of done:

- At least 20 labeled documentation queries exist.
- Eval output clearly shows pass/fail per query.
- Aggregate metrics are printed at the end.

## Priority 2: Citation And Grounding Checks

Goal:

Make sure final answers cite retrieved chunks and do not cite invented sources.

Implement:

- Parse final answers for chunk IDs in square brackets.
- Verify every cited chunk ID was included in the retrieved context.
- Flag answers with factual claims but no citations.
- Add deterministic tests for:
  - valid citation
  - missing citation
  - invented chunk ID
  - answer when no chunks are retrieved

Definition of done:

- Documentation answers cannot cite chunks that were not retrieved.
- Eval traces record citation validation status.

## Priority 3: Runtime Configuration Cleanup

Goal:

Make local commands work without fragile environment-variable workarounds.

Implement:

- Move the RAG DB path into central settings.
- Support `RAG_DB_PATH` from `.env`.
- Choose one default path:
  - project-local `var/rag/index.sqlite`
  - or explicit required env var
- Update README commands to match the actual behavior.

Definition of done:

- `uv run --no-editable agent-harness-rag` works from the project root, or fails
  with a clear message telling the user exactly which env var to set.

## Priority 4: Retrieved-Context Safety

Goal:

Treat retrieved HTML text as untrusted evidence, not instructions.

Implement:

- Add a lightweight scanner for retrieved chunks before model prompt assembly.
- Flag suspicious phrases such as:
  - `ignore previous instructions`
  - `system prompt`
  - `developer message`
  - `call this tool`
  - `exfiltrate`
- Decide policy:
  - exclude suspicious chunks
  - or include them with a warning field and stronger prompt wrapping
- Add trace events for retrieval-content screening.

Definition of done:

- Prompt-injection-like text inside docs is detected before model generation.
- Tests prove the model prompt does not treat retrieved docs as instructions.

## Priority 5: Embeddings

Goal:

Improve recall for paraphrased questions that keyword search misses.

Implement:

- Choose an embedding provider/model.
- Add an embeddings build step after FTS indexing.
- Store vectors by stable `chunk_id`.
- Store embedding metadata:
  - model name
  - vector dimension
  - created timestamp
  - index version
- Add validation that every embedding maps to an existing chunk.

Definition of done:

- Embeddings can be generated for an indexed corpus.
- Re-indexing unchanged chunks preserves their `chunk_id` and embedding mapping.

## Priority 6: Vector Search

Goal:

Retrieve semantically similar chunks using embeddings.

Implement:

- Start with NumPy brute-force cosine similarity.
- Normalize vectors before storing or searching.
- Return:
  - `chunk_id`
  - vector score
  - vector rank
- Add latency measurements.
- Consider FAISS only if NumPy is too slow for the full corpus.

Definition of done:

- Vector search returns top-k chunks for a query.
- Retrieval eval can compare keyword-only vs vector-only recall.

## Priority 7: Hybrid Ranking

Goal:

Combine keyword precision with semantic recall.

Implement:

- Run FTS search and vector search in parallel or sequentially.
- Merge results by `chunk_id`.
- Use Reciprocal Rank Fusion.
- Return:
  - keyword rank
  - vector rank
  - hybrid score
  - final rank

Definition of done:

- Retrieval eval reports:
  - FTS-only metrics
  - vector-only metrics
  - hybrid metrics
- Hybrid retrieval beats or matches both single-mode baselines on Recall@5.

## Priority 8: Index Size And Performance

Goal:

Keep full-corpus indexing fast and the SQLite database reasonably small.

Implement:

- Record index build time.
- Record DB size.
- Record docs/sec and chunks/sec.
- Consider external-content FTS if text duplication becomes too large.
- Run `optimize` / `VACUUM` only when the cost is acceptable.
- Keep batch and worker settings documented.

Definition of done:

- Full-corpus indexing has repeatable timing and size measurements.
- The indexer reports enough metrics to catch regressions.

## Priority 9: Incremental Re-Indexing

Goal:

Avoid rebuilding the full corpus when only a few docs change.

Implement:

- Compare `source_hash` for existing docs.
- Skip unchanged files.
- Re-index changed files.
- Remove docs for deleted files.
- Update metadata and index version.

Definition of done:

- Running the indexer twice on unchanged input skips most work.
- Changed files update their docs/chunks safely.

## Suggested Build Order

1. Retrieval eval set and Recall@K runner.
2. Citation validation.
3. Runtime config cleanup.
4. Retrieved-context safety scanner.
5. Index metrics and performance reporting.
6. Embeddings.
7. Vector search.
8. Hybrid ranking.
9. Incremental re-indexing.

