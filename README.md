# Agent Harness

A policy-gated, stateful agent pipeline for exploring approved Clarity HTML
documentation and drafting schema-grounded BigQuery SQL.

The project does not connect to BigQuery or patient data. Retrieval is local,
SQL execution is disabled, and generated SQL is returned only after deterministic
static validation.

## Architecture

One in-process LangGraph owns the request lifecycle:

```text
input policy -> intent
  |-> refusal
  |-> clarification (interrupt/resume)
  |-> general answer
  `-> retrieval permission
        |-> bounded host-authorized exploration
        `-> deterministic keyword retrieval
      -> context gate
        |-> documentation answer
        `-> query plan -> plan safety -> SQL generation -> SQL validation
              `-> bounded repair cycle
                    -> execution not configured
-> result safety -> citations -> final answer -> bounded follow-up
```

The working core includes policy screening, model-backed intent classification,
SQLite FTS retrieval, documentation answers, evidence-backed schema snapshots,
SQL generation, SQLGlot validation, traces, and evaluations. BigQuery dry-run,
cost approval, execution, result redaction, real authorization, semantic/vector
retrieval, and Python generation remain explicit future boundaries.

There are no internal MCP services and no fabricated data catalog.

## Repository Map

```text
src/
  agent_host/    LangGraph assembly, state, nodes, config, budgets, traces, CLI
  policy/        deterministic content and workflow policy
  retrieval/     HTML indexing, SQLite search, schema-evidence extraction, audit
  sql/           SQL models, generation, validation, disabled BigQuery adapter
  evals/         evaluation runner and deterministic assertions
  trace_viewer/  local trace rendering
tests/           unit and graph-routing tests
evals/           versioned evaluation cases
docs/            product and retrieval design
.local/          ignored indexes, traces, reports, and rendered artifacts
```

## Setup

```powershell
uv sync
Copy-Item .env.example .env
```

Configure the Anthropic gateway values in `.env`. Model-backed commands may
incur API usage.

## Index Documentation

```powershell
uv run agent-harness-rag-index <HTML_PATH> --limit 500 --workers 4 --bs 100
uv run agent-harness-rag-audit
```

The index defaults to `.local/rag/index.sqlite`. Override it with `RAG_DB_PATH`.
Approved proprietary HTML and generated SQLite files must remain uncommitted.

Production column chunks use the canonical GenQ parser: each documented column
is stored as its own SQLite/FTS chunk with the same logical chunk ID and passage
text used for query generation and semantic-retrieval evaluation. After a
parser or chunker version change, build a new database rather than modifying a
working index in place:

```powershell
uv run agent-harness-rag-index <HTML_PATH> --workers 4 --bs 500 `
  --db .local/rag/index-genq-columns.sqlite
$env:RAG_DB_PATH=".local/rag/index-genq-columns.sqlite"
uv run agent-harness-rag-audit
```

## Ask the Agent

```powershell
uv run agent-harness "What does ABN_ORDERS mean?"
uv run agent-harness "Write SQL to count appointments by status" --thread-id demo
```

The CLI handles clarification interrupts interactively. A thread ID retains
short-term state through LangGraph's in-memory checkpointer; it is not durable
across processes.

Render a trace:

```powershell
uv run agent-harness-traces .local/traces/<run_id>.jsonl --no-open
```

Run evaluations:

```powershell
uv run agent-harness-eval --suite smoke
uv run agent-harness-eval --suite red_team
uv run agent-harness-eval --suite intent --repetitions 3
uv run agent-harness-eval --suite retrieval --k 5 10
```

Reports default to `.local/evals/`. The retrieval suite needs a built index and
is described in [evals/retrieval/README.md](evals/retrieval/README.md); its
results are only comparable within one `chunker_version`.

## Verification

```powershell
uv run ruff format --check src tests
uv run ruff check .
uv run pyright
uv run pytest
uv build
```

## LangGraph Primitives Used

- `StateGraph`: declares nodes operating on the shared `AgentState`.
- Nodes: ordinary Python functions that return partial state updates.
- Fixed edges: define unconditional sequence.
- Conditional edges: route from policy, intent, context, and validation results.
- Reducers: append citations instead of overwriting them.
- `interrupt()`: pauses when clarification is required.
- `Command(resume=...)`: supplies the user's clarification and resumes the same
checkpoint.
- `InMemorySaver`: stores thread-scoped checkpoints for the current process.

JSONL is the single trace source. Each row is validated through a Pydantic
discriminated union before it is written; graph state does not duplicate trace
events.

LangGraph is used for control flow only. Prompt templates, output parsers,
chains, agent executors, memory, and retriever wrappers are not used: every
model call is a direct Anthropic SDK call with prompts declared in this
repository, so prompts and responses stay visible.

## Previous Architecture

Before ADR 001 this project ran five FastMCP services over localhost HTTP with
subprocess auto-start. That architecture is preserved and restorable at the
frozen `fallback/mcp-host` branch and the `archive/mcp-host-main-20260727` tag.
See [ADR 001](docs/adr/001-graph-trunk-and-mcp-boundary.md) for the rationale
and the restore procedure.

See [CLAUDE.md](CLAUDE.md) for contributor rules,
[docs/adr/](docs/adr/) for accepted decisions, [docs/PRD.md](docs/PRD.md) for
requirements, and [docs/RAG.md](docs/RAG.md) for retrieval design.
