# Agent Harness

A policy-gated agent host for observable data-science and analyst workflows,
real-schema documentation retrieval, SQL planning experiments, and
deterministic evaluation.

This repository does not execute against production data. Approved real-schema
documentation is indexed locally and remains uncommitted. Do not commit
credentials, PHI, proprietary schemas, production prompts, indexes, or
sensitive trace content.

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
       -> approved documentation retrieval over local RAG index
       -> table, column, and metric exploration over real indexed schemas
       -> SQL generation and validation services
  -> final output screen
  -> response plus JSONL trace
```

The classifier proposes a typed intent, not tools or execution actions. The
host derives the workflow and permitted tool contracts from its registry.
Execution limits are deterministic and are not inferred from model output or
live MCP descriptions.

Source code is organized directly by responsibility under `src/`:

- `agent_host/`: request lifecycle, workflow dispatch, model calls, execution
  budgets, tool authority, responses, and traces.
- `agent_host/workflows/`: one module per user-facing workflow.
- `retrieval/`: approved-HTML indexing, retrieval contracts, host client, and
  the read-only retrieval MCP server.
- `retrieval/audit.py`: local retrieval-index safety and integrity checks.
- `policy/`: deterministic input and model-context screening.
- `mcp_servers/`: intent classification, SQL generation, and SQL validation
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

Run all commands from the repository root.

### Build The Local RAG Index

Base command:

```powershell
uv run --no-editable agent-harness-rag-index <HTML_PATH> [flags]
```

Common flags:

```text
--limit <N>       Index only the first N HTML files.
--workers <N>     Number of parallel parser workers.
--bs <N>          Batch size for parser work before SQLite writes.
--db <PATH>       Output SQLite index path. Defaults to var/rag/index.sqlite.
```

Index a small approved-documentation subset for local testing:

```powershell
uv run --no-editable agent-harness-rag-index <HTML_PATH> --limit 500 --workers 4 --bs 100
```

The generated index defaults to `var/rag/index.sqlite`. Set `RAG_DB_PATH` to
use another local path. Do not commit SQLite index files.


### Start Services

Start only the processes needed for the workflow being exercised, each in its
own terminal.

Terminal 1, HTML retrieval MCP server:

```powershell
uv run --no-editable agent-harness-rag
```

The production RAG MCP surface is intentionally limited to:

- `retrieve_documentation_context`: bounded search plus cited chunk fetch.
- `find_table_doc`: exact table-document discovery.
- `get_doc_section`: bounded section retrieval for a named document.
- `search_columns`: bounded search over real indexed column information.

Lower-level search and arbitrary chunk-fetch helpers remain internal and are
not remotely callable MCP tools.

Terminal 2, intent classifier MCP server:

```powershell
uv run --no-editable agent-harness-intent
```

Terminal 3, SQL generation MCP server:

```powershell
uv run --no-editable agent-harness-sql-generation
```

Terminal 4, SQL validation MCP server:

```powershell
uv run --no-editable agent-harness-sql-validation
```

### Ask The Agent

Terminal 5, prompt agent:

```powershell
uv run --no-editable agent-harness "<question>" --json
```

Add `--viewer` to render and open the run trace.

Use `--viewer --no-open-viewer` to render without opening a browser.  Render an
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

The local RAG index is built from approved documentation files on disk. It is
not patient data, does not execute database queries, and must not be treated as
production authorization. SQL generation and validation are planning services;
the project does not run SQL against a real database.

This project does not provide production authorization, PHI protection, real
database execution, network isolation, or clinical decision support. See
[`docs/PRD.md`](docs/PRD.md) for the complete scope and acceptance criteria.

## Contributing

Read [`CLAUDE.md`](CLAUDE.md) before changing the repository. Keep changes
small, preserve deterministic boundaries, update tests with behavior changes,
and keep each document within its assigned responsibility.
