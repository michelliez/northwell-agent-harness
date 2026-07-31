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

Canonical documentation is a **closed set**: the requirement documents above,
ADRs, and READMEs beside the code they describe. `docs/reference/` is historical
and never updated. `tests/test_documentation_layout.py` warns when anything else
appears; it does not fail the build, because this is a norm rather than a wall.

Before adding a markdown file, place the content instead:

| Content | Home |
|---|---|
| A decision or trade-off | `docs/adr/NNN-short-title.md` |
| A changed contract | the one canonical document that owns it |
| A plan, roadmap, status, or TODO | the pull-request description or an issue |
| Superseded material | `docs/reference/` |

A plan stored in `docs/` silently claims to be current forever, and the next
reader cannot tell when it stopped being true. The same words in a pull-request
description are dated by construction. Prefer editing an existing document over
adding one, and deleting a wrong document over annotating it.

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
  implemented and tested (422 tests passing). User-facing layer pending.
- All execution limits live in `agent_host/budget.py`, not configuration.
- Generated artifacts live under ignored `.local/`.
- Never commit credentials, PHI, proprietary schemas or HTML, SQLite indexes,
  sensitive traces, or real query results.

## Ownership

```text
agent_host/    graph lifecycle, state, nodes, config, budget, trace, CLI
policy/        deterministic screening and policy results
retrieval/     HTML parsing, indexing, search, evidence extraction, index audit
retrieval/genq/ offline synthetic-query generation; never in the request path
sql/           SQL domain models, generation, validation, BigQuery boundary
               dry_run, read-only executor, result safety, and audit logging
               exist as adapters; no graph route reaches them
evals/         evaluation execution, assertions, dataset splits, query filters
trace_viewer/  trace parsing, redaction, and rendering
```

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
