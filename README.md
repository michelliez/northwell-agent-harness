# Agent Harness

A policy-gated agent host for observable workflows, documentation retrieval,
SQL planning experiments, and deterministic evaluation.

This repository uses synthetic data and localhost services. Do not commit
credentials, PHI, proprietary schemas, production prompts, or sensitive trace
content.

## Documentation

- [`CLAUDE.md`](CLAUDE.md) is the context entrypoint for contributors and
  coding agents.
- [`docs/PRD.md`](docs/PRD.md) owns product requirements and acceptance
  criteria.
- [`docs/RAG.md`](docs/RAG.md) owns the retrieval design and implementation
  plan.

Documents elsewhere under `docs/` are research or planning references unless
one of the canonical documents explicitly incorporates them.

## Architecture

```text
request
  -> deterministic policy screen
  -> intent classification
  -> host-owned workflow dispatch and execution budget
       -> general response
       -> documentation retrieval
       -> temporary mock catalog/SQL workflow
  -> final output screen
  -> response plus JSONL trace
```

The model may propose an action; the host owns execution authority. Tool
contracts and execution limits are deterministic and are not inferred from
model output or live MCP descriptions.

Source code is organized directly by responsibility under `src/`:

- `agent_host/`: request lifecycle, workflow dispatch, model calls, execution
  budgets, tool authority, responses, and traces.
- `agent_host/workflows/`: one module per user-facing workflow.
- `retrieval/`: approved-HTML indexing, retrieval contracts, host client, and
  the read-only retrieval MCP server.
- `policy/`: deterministic input and model-context screening.
- `mcp_servers/`: intent classification and temporary mock catalog/SQL
  services.
- `evals/`: deterministic evaluation runners and assertions.
- `trace_viewer/`: trace parsing, redaction, classification, and rendering.

Generated local state lives outside `src/`. In particular,
`var/rag/index.sqlite` is the ignored local retrieval index.

## Setup

```powershell
uv sync
Copy-Item .env.example .env
```

Fill in `.env` with approved local configuration. Do not commit it.

Use `uv run --no-editable` for installed project commands. This avoids an
editable-install import issue observed on some Python 3.14 environments.

## Run Locally

Start only the processes needed for the workflow being exercised, each in its
own terminal.

Temporary mock catalog and SQL services:

```powershell
uv run --no-editable agent-harness-data-catalog
uv run --no-editable agent-harness-sql-generation
uv run --no-editable agent-harness-sql-validation
```

Intent classifier:

```powershell
uv run --no-editable agent-harness-intent
```

Documentation retrieval:

```powershell
uv run --no-editable agent-harness-rag-index path\to\approved-doc.html
uv run --no-editable agent-harness-rag
```

The generated index defaults to `var/rag/index.sqlite`. Set `RAG_DB_PATH` to
use another local path.

Run the agent:

```powershell
uv run --no-editable agent-harness "What data would I need to answer visit counts?" --json
```

Add `--viewer` to render and open the run trace, or
`--viewer --no-open-viewer` to render without opening a browser. Render an
existing trace with:

```powershell
uv run --no-editable agent-harness-traces logs/runs/<run_id>.jsonl
```

Model-backed commands can incur API usage. Run them only when that usage is
approved.

## Verification

Unit tests and static checks do not require live MCP servers or model calls:

```powershell
uv run ruff format --check src tests
uv run ruff check src tests
uv run pyright
uv run pytest
```

Run a single test with:

```powershell
uv run pytest tests/test_tool_registry.py -q
```

The live evaluation suites require their corresponding MCP services and may
make model calls:

```powershell
uv run --no-editable agent-harness-eval --suite smoke
uv run --no-editable agent-harness-eval --suite red_team
uv run --no-editable agent-harness-eval --suite intent --repetitions 3
```

Evaluation results are written under `evals/results/` and are ignored by Git.

## Scope Boundary

The catalog and its SQL path are temporary synthetic fixtures. They are not the
future retrieval architecture and must not become a source of truth for real
schemas. The planned workflow retrieves approved documentation and schema
evidence before generating or validating SQL.

This project does not provide production authorization, PHI protection, real
database execution, network isolation, or clinical decision support. See
[`docs/PRD.md`](docs/PRD.md) for the complete scope and acceptance criteria.

## Contributing

Read [`CLAUDE.md`](CLAUDE.md) before changing the repository. Keep changes
small, preserve deterministic boundaries, update tests with behavior changes,
and keep each document within its assigned responsibility.
