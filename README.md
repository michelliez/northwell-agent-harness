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
        `-> Claude QueryPlanAST -> deterministic plan safety
              -> approved plan -> deterministic SQL compiler -> SQL validation
              `-> bounded Claude repair -> SQL validation
                    -> execution not configured
-> result safety -> citations -> final answer -> bounded follow-up
```

The working core includes policy screening, model-backed intent classification,
SQLite FTS retrieval, documentation answers, evidence-backed schema snapshots,
typed plan proposals, deterministic plan authorization and BigQuery SQL
compilation, named query parameters, plan-aware SQLGlot validation, bounded
repairs, traces, and evaluations.

Execution, result redaction, real identity/role authorization, semantic/vector
retrieval, and Python generation are future work. Dry-run and cost approval have
adapters and tests but no graph route. The rest have no code at all — a
placeholder that only raises reads as partial support, so the boundary gets
written when the integration is real.

There are no internal MCP services and no fabricated data catalog.

## Repository Map

```text
src/
  agent_host/    LangGraph assembly, state, nodes, config, budgets, traces, CLI
  policy/        deterministic content and workflow policy
  retrieval/     HTML parsing and indexing, SQLite search, schema evidence, audit
    genq/        offline synthetic-query generation (never in the request path)
  sql/           SQL models, planning, compilation, validation, BigQuery adapter
  evals/         evaluation runner, assertions, dataset splits and query filters
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

Optional dependency groups are not needed to run the agent:

| Group | Installs | Needed for |
|---|---|---|
| `bigquery` | `google-cloud-bigquery`, `pyarrow` | `sql/bigquery_adapter.dry_run` and its tests |
| `genq` | Torch, Transformers, FAISS, SentenceTransformers | offline synthetic-query generation |

`uv sync --group bigquery` enables the dry-run adapter. Without it the graph
still runs unchanged — no node imports the BigQuery SDK, and `dry_run` fails
closed with an install hint. Tests for those paths skip rather than fail.

## Index Documentation

```powershell
uv run agent-harness-rag-index <HTML_PATH> --limit 500 --workers 4 --bs 100
uv run agent-harness-rag-audit
```

The index defaults to `.local/rag/index.sqlite`. Override it with `RAG_DB_PATH`.
Approved proprietary HTML and generated SQLite files must remain uncommitted.

Production column chunks come from `retrieval/column_parser.py`: each documented
column is stored as its own SQLite/FTS chunk with the same logical chunk ID and
passage text that offline query generation and semantic-retrieval evaluation
use. After a parser or chunker version change, build a new database rather than
modifying a working index in place:

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


# Running the FastAPI Backend

## Quick Start

**Development mode** (with auto-reload):

```bash
uv run uvicorn api.main:app --app-dir src --reload --reload-dir src --port 8000
```

Then visit: `http://localhost:8000/docs` (Swagger UI)

**Production mode**:

```bash
uv run uvicorn api.main:app --app-dir src --port 8000 --workers 1
```

## Environment Setup

Create a `.env` file:

```bash
ANTHROPIC_API_KEY=<your-key>
GOOGLE_CLOUD_PROJECT=<your-project>
BIGQUERY_LOCATION=us-west1
ALLOWED_ORIGINS=http://localhost:8501,http://localhost:3000
```

Or set environment variables:

```bash
export ANTHROPIC_API_KEY=<your-key>
export GOOGLE_CLOUD_PROJECT=<your-project>
```

## API Endpoints

### Health Check
```bash
curl http://localhost:8000/health
```

### Ask a Question
```bash
curl -X POST http://localhost:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Count appointments by department",
    "user_id": "jane.smith"
  }'
```

### Resume with Approval Token
```bash
curl -X POST http://localhost:8000/api/v1/resume \
  -H "Content-Type: application/json" \
  -d '{
    "run_id": "uuid-from-previous-ask",
    "approval_token": "token-from-cost-gate"
  }'
```

### Get Audit Trail for a Run
```bash
curl http://localhost:8000/api/v1/audit/{run_id}
```

### Get Audit Summary
```bash
curl 'http://localhost:8000/api/v1/audit/summary?start_date=2026-07-30T00:00:00Z&user_id=jane.smith'
```

## API Documentation

Visit `http://localhost:8000/docs` for interactive Swagger UI with all endpoints, request/response schemas, and try-it-out functionality.

## Files Structure

```
src/api/
├── __init__.py
├── main.py              # FastAPI app entry point
├── models.py            # Request/response Pydantic models
├── README.md            # API documentation
└── routes/
    ├── __init__.py
    ├── ask.py           # /ask and /resume endpoints
    └── audit.py         # /audit endpoints
```

## What the API Does

1. **Receives questions** via `/api/v1/ask`
2. **Calls the LangGraph workflow** directly (no separate process)
3. **Records all events** in JSONL audit logs
4. **Returns workflow trace + results** to caller
5. **Allows resumption** with approval tokens for expensive queries
6. **Provides audit log access** via `/api/v1/audit`

## Chat frontends

Chainlit is the primary chat interface. It calls the versioned FastAPI API and
keeps only the opaque conversation thread ID in its in-memory browser session.
Start FastAPI, then start Chainlit in a second terminal:

```bash
uv run uvicorn api.main:app --app-dir src --reload --reload-dir src --port 8000
uv run agent-harness-ui -w --port 8501
```

The existing Streamlit prototype remains available and unchanged:

```bash
uv run streamlit run src/ui/app.py --server.port 8502
```

See `src/ui/README.md` for frontend configuration and privacy behavior.

## Troubleshooting

**"ModuleNotFoundError: No module named 'anthropic'"**
- Run: `uv sync` to install dependencies

**"ANTHROPIC_API_KEY not set"**
- Set the environment variable or add to `.env`

**"Connection refused on port 8000"**
- API is not running. Start it with: `uv run uvicorn api.main:app --app-dir src --reload --reload-dir src --port 8000`

**Swagger UI not available**
- Visit `http://localhost:8000/docs` (lowercase)
- Alternative: `http://localhost:8000/redoc`

## Testing the API

```bash
# Run all tests
uv run pytest tests/ -v

# Run API-specific tests
uv run pytest tests/api/ -v

# Run with coverage
uv run pytest tests/ --cov=src/api
```

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
- Reducers: append citations and SQL repair history instead of overwriting them.
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
