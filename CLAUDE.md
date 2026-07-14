# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an **agent evaluation harness spike** — a proof-of-concept for a multi-component agentic system that routes user requests through policy gates, intent classification, and bounded tool calls. The goal is to validate safe, observable agent routing before production integration with Epic and BigQuery.

**Do not commit proprietary data, credentials, PHI, schema exports, or logs containing prompts to this repo.** It is a non-enterprise POC designed to be copyable into an enterprise environment later.

## Commands

### Setup

```bash
uv sync
cp .env.example .env
# Fill in .env with Anthropic API keys and AI Hub config
```

**Important:** Use `uv run --no-editable` for all spike commands. On Python 3.14/macOS, editable installs can be marked hidden, causing `ModuleNotFoundError`.

### Running the System

The system has multiple components that run in parallel. **Start each in its own terminal:**

**Terminal 1 — Data catalog MCP server** (provides dummy table metadata):
```bash
uv run --no-editable nh-spike-data-catalog
# Starts at http://localhost:8000/mcp
```

**Terminal 2 — Intent classifier MCP server** (routes requests to tool categories):
```bash
uv run --no-editable nh-spike-intent
# Starts at http://localhost:8002/mcp
```

**Terminal 3 — Agent CLI** (orchestrates the full pipeline):
```bash
uv run --no-editable nh-spike-agent "What data would I need to answer how many patients had visits last month?" --json
```

### Testing

Run unit tests (no MCP servers or AI Hub calls required):
```bash
uv run --no-editable pytest -q
```

Run a single test:
```bash
uv run --no-editable pytest tests/policy_gate_test.py::test_blocked_by_identifier -xvs
```

Run evaluations (requires both MCP servers running):
```bash
uv run --no-editable nh-spike-eval --suite smoke
uv run --no-editable nh-spike-eval --suite red_team
uv run --no-editable nh-spike-eval --suite intent --repetitions 3
```

## Architecture

The system is split into **four independent components** that communicate via REST/HTTP at fixed localhost ports:

### 1. **Agent Host** (`src/harness_spike/agent_host/`)
- **Central orchestrator** for the request→response loop
- Runs the policy gate (deterministic safety checks)
- Calls the Anthropic model via the Anthropic SDK
- Bridges the model to MCP tool servers
- Logs a detailed trace of every decision to `logs/runs/<timestamp>.jsonl`

**Key files:**
- `agent.py`: Main logic—`answer_question()` orchestrates policy → intent → agent loop
- `cli.py`: CLI entrypoint, also used by the evaluator
- `mcp_bridge.py`: Async HTTP client to query MCP servers
- `trace_logger.py`: Records every decision step for evaluation
- `schemas.py`: Pydantic models for request/response contracts

### 2. **Policy Gate** (`src/harness_spike/policy/gates.py`)
- **First defense:** runs before model or tool routing
- Lexical blocklist of identifier/PHI terms (e.g., "patient name", "ssn", "mrn")
- Returns `{allowed: bool, reason: str | None, matched_term: str | None}`
- If a request is blocked, the agent host returns a refusal without calling the model

**Key invariant:** Every blocked request must have zero downstream events in its trace (no intent, model, or catalog calls).

### 3. **Intent Classifier** (`src/harness_spike/mcp_servers/intent.py`)
- **Lightweight router:** classifies user intent into categories before catalog access
- Returns `{intent: str, action: str, confidence: float, reasoning: str, clarification: str | None}`
- Uses the configured Anthropic model to classify but does not answer questions or access catalog
- Intended intents: `"schema_lookup"`, `"safe_sql_generation"`, `"unknown"`, `"refuse"`
- Low confidence or `"refuse"` intent should stop before catalog access (not yet enforced by host)

**Key invariant:** Intent classification is always read-only and does not call the data catalog.

### 4. **MCP Tool Servers** (`src/harness_spike/mcp_servers/`)
- Implement the Model Context Protocol (MCP) — a standard for exposing tools to LLM agents
- Each runs as a standalone FastMCP HTTP server on its own port
- The agent host discovers tools from MCP, the model decides which to call, the host executes them

**Current servers:**
- **`data_catalog.py`** (port 8000): Dummy tools — `search_tables()`, `get_table_schema()`, `search_docs()`, `get_table_info()`
- **`intent.py`** (port 8002): Intent classification service
- **`sql_generation.py`** (port 8003): Placeholder for future SQL generation validation
- **`sql_validation.py`** (port 8004): Placeholder for future SQL validation

Adding a new tool: Add a function with `@mcp.tool` decorator in `src/harness_spike/mcp_servers/`, then register a script entry point in `pyproject.toml`.

### 5. **Evaluation Harness** (`src/harness_spike/evals/`)
- Runs **deterministic test suites** against the agent host
- Reads JSONL case files under `evals/`; each case specifies expected behavior
- Runs the full request→response pipeline and asserts on:
  - Policy decision (allowed/blocked)
  - Intent classification accuracy
  - Which catalog tools were called
  - Claims made in the final answer (grounding checks)
- Reports are written to `evals/results/` (git-ignored)

**Key files:**
- `runner.py`: CLI entrypoint, loads cases, runs the agent, evaluates assertions
- `assertions.py`: Deterministic checks on response traces
- `intent_assertions.py`: Specialized checks for intent classifier accuracy

**Suites:**
- `smoke`: Small regression suite; should pass in seconds
- `red_team`: Broader adversarial suite; includes PHI bypass attempts, prompt injection, intent misrouting
- `intent`: Direct testing of the intent classifier (no agent host)

## Key Design Patterns

### Policy → Intent → Agent

Every request follows this sequence:

1. **Policy gate** (deterministic, no model) — blocks requests matching PHI/identifier terms
2. **Intent classification** (model call, no catalog) — categorizes the request to guide tool selection
3. **Agent loop** (model + tools) — the model decides which tools to call based on intent; the host executes and loops

This separation ensures:
- Policy decisions are fast and predictable
- Intent classification is focused and cacheable
- Tool calls are bounded by the model's decision and the host's enforcement

### Trace Logging

Every request produces a trace file at `logs/runs/<uuid>.jsonl`. Each line is a JSON event:

```json
{"event": "request.received", "question": "..."}
{"event": "policy.checked", "allowed": true, "matched_term": null}
{"event": "intent.requested", "intent": "schema_lookup"}
{"event": "tools.discovered", "tool_names": ["search_tables", "get_table_schema"]}
{"event": "model.response", "tool_calls": [...]}
{"event": "tool.executed", "tool": "search_tables", "result": {...}}
{"event": "request.answered", "answer": "..."}
```

The evaluator reads these traces to verify behavior without re-running the model or tools.

### Red-Team Corpus Structure

Two complementary test collections:

- **`tests/policy_gate_test.py`** — fast, deterministic unit tests. Exhaustive variants of blocked requests: direct identifiers, typos, leetspeak, zero-width characters, punctuation. No MCP servers or API calls.
- **`evals/red_team.jsonl`** — small end-to-end JSONL corpus (typically 12–18 cases). Representative scenarios that verify policy blocks before intent/catalog, and safe requests route correctly. Requires both MCP servers and Anthropic API.

Each JSONL case names its expected policy decision, intent, catalog calls, and final outcome. All cases use synthetic prompts (no PHI or production data).

## Configuration

Settings are loaded from `.env` (see `.env.example`):

- `ANTHROPIC_API_KEY` or `AI_HUB_API_KEY` — authentication
- `ANTHROPIC_BASE_URL` — AI Hub endpoint (optional, defaults to Anthropic's public API)
- `CLAUDE_MODEL` — model to use (default: `claude-haiku-4-5-20251001`)
- `MCP_SERVER_URL` — data catalog server address (default: `http://localhost:8000/mcp`)
- `INTENT_MCP_URL` — intent classifier server address (default: `http://localhost:8002/mcp`)
- `MAX_TOOL_ROUNDS` — max iterations of the agent loop (default: 3)
- `TRACE_DIR` — where to write trace JSONLs (default: `logs/runs`)
- `LOG_RAW_PROMPTS` — log full prompts to trace (default: false, for privacy)

## Development Guidelines

### Adding a New Tool

1. Add a function with `@mcp.tool` decorator in one of the `mcp_servers/*.py` files
2. Write a clear docstring explaining when the model should use this tool
3. If adding a new MCP server, register a script entry in `pyproject.toml` under `[project.scripts]`
4. Add synthetic test cases to `evals/red_team.jsonl` or `tests/policy_gate_test.py`

### Adding a Policy Rule

1. Edit `src/harness_spike/policy/gates.py` — add terms to `IDENTIFIER_TERMS` or implement a new rule
2. Add test cases to `tests/policy_gate_test.py` covering:
   - Direct match (e.g., "patient name")
   - Typos, punctuation, leetspeak variants
   - False positives you want to allow
3. After editing `gates.py`, rebuild the non-editable package before testing:
   ```bash
   uv sync --reinstall-package nw-harness
   ```

### Adding an Evaluation Case

1. Create a JSONL case under `evals/` with fields: `id`, `category`, `prompt`, `expected_policy`, `expected_intent`, `expected_catalog_calls`, `required_claims`, `expected_outcome`
2. Run the evaluator to see if your case passes: `uv run --no-editable nh-spike-eval --suite red_team`
3. If evaluation fails, check the report under `evals/results/` for assertion details

### Testing a Single Component

- **Policy gate only:** `pytest tests/policy_gate_test.py` (no MCP servers needed)
- **Intent classifier only:** Start only `nh-spike-intent`, then `pytest tests/test_intent_classifier.py`
- **Agent host:** Start both `nh-spike-data-catalog` and `nh-spike-intent`, then `pytest tests/test_agent_messages.py`
- **Evaluations:** Start both MCP servers, then `uv run --no-editable nh-spike-eval --suite smoke`

## Known Limitations

This is a proof-of-concept on localhost with dummy data. It demonstrates:

- ✅ Safe request routing with policy gates
- ✅ Observable tool usage via traces
- ✅ Deterministic evaluation of agent behavior

It does **not** demonstrate:

- ❌ Production authorization or database access controls
- ❌ Real SQL execution or BigQuery integration
- ❌ Network isolation or encrypted inter-process communication
- ❌ PHI protection or encryption at rest

The intent classifier's `"refuse"` recommendation is not yet enforced by the host. Catalog failures may not produce invented facts in the final answer, but this is not guaranteed.

## Next Steps

- **Host enforcement:** Make `recommended_action="refuse"` stop the agent before catalog access
- **Grounding expansion:** Add more synthetic cases testing table/column claim verification
- **Safe SQL workflow:** Implement SQL generation and validation for allowed requests
- **Production integration:** Wire Epic, BigQuery, and network isolation controls
