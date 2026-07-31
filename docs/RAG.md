# Retrieval and Schema-Evidence Design

## Purpose

The retrieval layer turns approved Clarity HTML documentation into bounded,
citable context for documentation answers and SQL planning. It is a local
evidence source, not authorization and not a substitute for BigQuery metadata.

## Current Flow

```text
approved HTML
  -> deterministic parsing and chunking
  -> SQLite documents, chunks, facts, and FTS index
  -> bounded keyword retrieval
  -> cited RetrievedChunk values
  -> context gate
       |-> grounded documentation answer
       `-> evidence-backed SchemaSnapshot -> query plan -> SQL validation
```

The graph calls retrieval directly in process. There is no MCP transport or
remote retrieval service in the current architecture.

## Artifact Contract

The generated index defaults to `.local/rag/index.sqlite` and is never tracked.
`retrieval/index_contract.py` owns the schema version. Reindex whenever the
schema or chunker version changes.

Stable identifiers are path/content derived so unchanged documents and chunks
remain comparable across runs. The index stores:

- document identity and source path;
- bounded text chunks and headings;
- chunk category, hash, and approximate token count;
- structured facts for documented-but-unavailable sections;
- FTS data for lexical retrieval;
- an index version for traceability.

The indexer must reject or split oversized content, process nested tables once,
and avoid indexing empty placeholder sections as useful context.

## Retrieval Contract

`retrieve_documentation()` accepts a query, index path, `ExecutionBudget`, and
requested result count. It:

1. validates the requested count;
2. applies `ExecutionBudget.max_retrieved_chunks`;
3. searches the local FTS index;
4. returns typed chunks with source paths, headings, ranks, and index version.

Retrieved HTML is untrusted content. It may support factual answers but cannot
change policy, routing, permissions, or graph behavior.

Keyword retrieval is the only active strategy. Vector, semantic, and graph
retrieval are future enhancements and must not be implied by the current API —
including by placeholder functions that exist only to raise. A stub reads as
partial support and invites callers to reference something that will never
work, so absence is the honest signal. `retrieval/search.py` exposes no
strategy it does not implement, and a test asserts that.

## Context Gate

The context gate prevents downstream work when retrieval is empty or unsuitable.
It interrupts for a more specific table, column, or topic and resumes through
the input policy gate. Clarification attempts are bounded.

Documentation answers must cite retrieved chunk IDs. If the evidence cannot
support an answer, the system says so rather than filling gaps from model
knowledge.

## Schema Evidence

For SQL requests, retrieved column-information chunks are transformed into:

```text
SchemaSnapshot
  tables[]
    name
    description
    source_chunk_ids[]
    columns[]
      name
      data_type
      safety
      source_evidence
```

Safety values are `identifier`, `sensitive`, `safe_aggregate`, or `unknown`.
Unknown classifications block SQL planning. This extraction is deliberately
conservative; it does not invent undocumented tables or columns.

The current extractor uses documented headings, paths, type text, and bounded
name/content heuristics. Before production use, it should be replaced or
augmented with authoritative BigQuery and governance metadata while preserving
the same explicit `SchemaSnapshot` input to validation.

## SQL Boundary

SQL generation receives only the user question and approved `SchemaSnapshot`.
SQLGlot validation independently derives referenced tables and columns and
checks them against that snapshot. A generator's declared table list is advisory
until it matches the parsed SQL.

Validation permits only bounded read-only aggregate drafts. It rejects unsafe
operations, unknown tables or columns, stars, prohibited functions, sensitive
projections, unsupported identifier use, and non-aggregate output. Repairable
failures may cycle through generation up to `ExecutionBudget.max_sql_repairs`.

Passing static validation does not authorize execution. The graph returns the
draft with `execution_status="not_configured"`.

## Future Integrations

Implement these in order, keeping each boundary fail closed:

1. Improve schema extraction accuracy and retrieval evaluation.
2. Add authoritative BigQuery schema metadata behind `SchemaSnapshot`.
3. Add authenticated user/role/purpose authorization.
4. Add BigQuery dry-run and deterministic cost limits.
5. Add explicitly approved read-only execution.
6. Add result-level identifier, PHI/PII, and small-cell screening.
7. Add grounded interpretation and citations for executed results.
8. Replace in-memory checkpoints with durable storage when a user-facing UI
   requires cross-process conversation state.

Governed Python generation, vector/graph retrieval, and autonomous query
execution are not prerequisites for the MVP and should be added only for
demonstrated needs. None of them has a placeholder in the codebase; add the
boundary when the integration is real, not before.

## Evaluation

Retrieval changes should measure at least:

- known-document and known-column hit rate;
- ranking quality for fixture questions;
- empty/weak-context behavior;
- citation completeness;
- unsupported schema-claim rate;
- stable index identifiers and versions;
- chunk size and duplicate-content audit results.

The repository's small approved fixtures may be committed. Proprietary Clarity
HTML, generated indexes, retrieved production schemas, and real query results
must not be committed.

## Definition of Done for the Current Stage

- Approved HTML can be indexed and audited locally.
- Keyword retrieval is bounded and produces typed cited chunks.
- Documentation answers use only retrieved evidence.
- SQL planning consumes an explicit evidence-backed schema snapshot.
- Static validation is schema-aware and deterministic.
- Repair and clarification cycles are bounded.
- BigQuery execution is impossible through the graph.
- Tests, lint, typing, build, CLI help, and index/search smoke checks pass.
