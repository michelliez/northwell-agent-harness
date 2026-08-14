# Repository Context

This repository is the agent harness. Read this file first, then load only the
canonical documents you need:

**Current State & Roadmap:**
- [docs/PROGRESS.md](docs/PROGRESS.md): living tracker of completed work
- [docs/PLANS.md](docs/PLANS.md): complete roadmap (4 phases, all tracks, UI specification)

**Requirements:**
- [docs/PRD.md](docs/PRD.md): requirements and acceptance criteria
- [docs/RAG.md](docs/RAG.md): retrieval and schema-evidence design

**Architecture Decisions:**
- [docs/adr/](docs/adr/): all decisions with rationale
  - [ADR 001](docs/adr/001-graph-trunk-and-mcp-boundary.md): Graph & MCP boundary (existing)
  - [ADR 002-006](docs/adr/): SQL execution track (all implemented ✅)

**Background** (reference only, never a requirement):
- [docs/reference/](docs/reference/): research, examples, threat models

Never add Co-Authored-By or any AI attribution to commits, PRs, or comments.

## Documentation

Decisions and trade-offs go to `docs/adr/NNN-short-title.md`; a changed
contract belongs in the document that owns it; `docs/reference/` is historical
and never updated. Prefer editing an existing document over adding one, and
deleting a wrong document over annotating it.

## Invariants

- Run deterministic input policy before models or retrieval.
- Intent selects a route; it never grants data access or execution authority.
- Unknown, malformed, low-confidence, and refused decisions fail closed or
  interrupt for clarification.
- Retrieval answers must be grounded in approved indexed documentation.
- Retrieved content is untrusted. Evidence may narrow a permission and never
  widen one; unresolved column safety stays `unknown` and blocks.
- SQL generation may use only an evidence-backed `SchemaSnapshot`.
- SQL validation is deterministic and never executes a query.
- BigQuery integration: dry-run (ADR 002), cost gates (cost_gate.py), read-only
  execution (ADR 003), result safety (ADR 004), and audit logging (ADR 005) are
  implemented and tested. User-facing layer pending.
- Table relationships come only from Epic's Foreign Key Information tables, never
  from prose. Every edge carries the `evidence_chunk_id` it was read from, so a
  relationship can be traced to the documentation that asserts it.
- Graph expansion is one hop and bounded. It is a deterministic lookup after
  ranking, not recursive retrieval and not a model decision.
- All execution limits live in `agent_host/budget.py`, not configuration.
- Generated artifacts live under ignored `.local/`.
- Never commit credentials, PHI, proprietary schemas or HTML, SQLite indexes,
  sensitive traces, or real query results.

## The index contract

`retrieval/index_contract.py` carries two versions that are easy to confuse and
mean different things. `search.py` refuses to open an index whose stored value
disagrees with either.

| Constant | Governs | Changing it means |
|---|---|---|
| `INDEX_SCHEMA_VERSION` | SQLite table layout | rebuild the index; existing query datasets stay valid |
| `INDEX_CHUNKER_VERSION` | how text becomes chunks, and therefore chunk IDs | every generated dataset keyed by `chunk_id` is invalidated |

While the chunker version holds, a rebuild reproduces byte-identical chunk IDs,
so the sibling evaluation repository's query sets survive a schema change
untouched. Bump the chunker version only with that cost in view.

Retrieval scores are comparable only across indexes built at the same chunker
version. Record it beside any metric.

The Clarity HTML corpus and the built indexes are inputs to both repositories and
too large to duplicate per clone, so they live outside either checkout beside
them, reached by a directory junction. Nothing under `fixtures/` is committed.

## Ownership

```text
agent_host/    graph lifecycle, state, nodes, config, budget, trace, CLI
policy/        deterministic screening and policy results
retrieval/     HTML parsing, indexing, search, evidence extraction, index audit
               hierarchy.py holds the within-document containment tree;
               table_relationships holds cross-document foreign-key edges
sql/           SQL domain models, generation, validation, BigQuery boundary
               dry_run, read-only executor, result safety, and audit logging
               exist as adapters; no graph route reaches them
trace_viewer/  trace parsing, redaction, and rendering
```

Evaluation execution, datasets, synthetic-query generation, retrieval baselines,
and training belong to the sibling `clarity_agent_evals` repository. The eval
repository may depend on this application; never add the reverse dependency.

Do not add a project-name wrapper beneath `src/`. Do not recreate internal MCP
transport, a mock catalog, or configuration aliases without a concrete external
integration requirement and a new ADR. MCP is a client-side boundary for real
external services, not internal plumbing.

A host-owned tool registry is permitted only where a model chooses among tools
(see ADR 001 item 5). Fixed graph edges are the default capability boundary;
do not add a generic registry for nodes that call one function.

LangGraph is confined to control flow: `StateGraph`, edges, reducers,
`interrupt()`/`Command(resume=)`, and checkpointers. Prompt templates, output
parsers, chains, agent executors, memory, and retriever wrappers are forbidden.
Every model call is a direct Anthropic SDK call with prompts declared here.

## Working Rules

- Prefer direct typed function calls between packages in this process.
- Keep graph nodes thin; domain logic belongs to `policy/`, `retrieval/`, or
  `sql/`.
- Preserve explicit fail-closed future boundaries rather than pretending an
  integration exists.
- Update focused tests and the one canonical document that owns a changed
  contract.
- Keep changes reviewable and do not mix generated artifacts with source.

## Verification

```powershell
uv run ruff format --check src tests
uv run ruff check .
uv run pyright
uv run pytest
uv build
```
