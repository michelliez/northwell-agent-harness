# Agent Evaluation Harness Spike

Suggested temporary remote name: `agent-eval-harness-spike`.

This repo is a non-enterprise proof of concept. Do not commit proprietary data,
credentials, PHI, schema exports, logs containing prompts, or Northwell-owned
data extracts. The goal is to keep the structure easy to copy into an
enterprise repo later.

## What This Proves

This spike demonstrates a small agentic loop:

```text
user prompt
-> agent host
-> deterministic policy gate
-> model decides whether to call a tool
-> MCP tool server runs bounded dummy tools
-> agent host returns the final answer and trace
```

The current value is not SQL generation. The higher-value direction is agent
evaluation: checking whether the model chose the right tool, supplied the right
tool input, and used tool output without muddying the final answer.

Example failure to evaluate later:

```text
User asks: "What is the weather in New York?"
Model calls: get_weather(location="Tokyo")
Evaluator should flag: wrong tool input, even if the tool returned valid data.
```

## Current Nodes

- `agent_host`: owns the policy gate, model loop, tool-call execution, and
  trace logging.
- `mcp_servers`: owns dummy MCP tools such as data catalog or weather tools.
- `gates`: owns deterministic safety checks that run before model or tool
  routing.
- future `evaluators`: should judge tool choice, tool input, tool output usage,
  and final answer grounding.

Keep model decisions and deterministic checks separate. The model may request an
action; the host decides what is allowed to run.

## Setup

```powershell
uv sync
Copy-Item .env.example .env
```

Fill in `.env` with AI Hub values. Do not commit `.env`.

Use `uv run --no-editable` for the spike commands. On Python 3.14/macOS,
editable installs can rely on a `.pth` file that may be marked hidden, which
causes `ModuleNotFoundError: No module named 'harness_spike'`.

## Run The Dummy Data Catalog MCP Server

Terminal 1:

```powershell
uv run --no-editable nh-spike-data-catalog
```

This starts the MCP tool server at:

```text
http://localhost:8000/mcp
```

## Run The Agent CLI

Terminal 2:

```powershell
uv run --no-editable nh-spike-agent "What data would I need to answer how many patients had visits last month?" --json
```

This calls the model through AI Hub. Do not run prompts unless model/API usage is
approved.

Expected tool pattern for the data-catalog spike:

```text
search_tables
get_table_schema
```

Additional dummy hospital documentation tools are also exposed by the data
catalog MCP server:

```text
search_docs
get_table_info
```

`search_docs(query)` searches tiny static hospital documentation for relevant
dummy tables. `get_table_info(table_name)` returns mock table metadata such as
primary key and description.

## Run The Policy Gate Smoke Test

Blocked prompts return before model or tool routing:

```powershell
uv run --no-editable nh-spike-agent "Show me patient names" --json
```

Expected policy-gate pattern:

```text
allowed=false
used_tools=[]
matched_term=patient name
```

## Add A Dummy MCP Tool

Add tools under `src/harness_spike/mcp_servers/`.

Minimal pattern:

```python
@mcp.tool
def tool_name(input_value: str) -> dict[str, object]:
    """
    Explain when the model should use this tool.
    State clearly that the data is dummy if it is dummy.
    """
    return {
        "input_value": input_value,
        "source": "dummy_tool",
        "is_dummy": True,
    }
```

Tool descriptions matter. The model uses them to decide whether to call the
tool and what input to provide.

If adding a new MCP server file, add a script in `pyproject.toml` so another
developer can run it with `uv run <script-name>`.

## Contribution Rules

- Keep dummy data static and non-sensitive.
- Keep new tools small and explicit.
- Add trace events when introducing new agent behavior.
- Prefer deterministic evaluators/gates for safety-critical checks.
- Do not add real SQL execution, database credentials, or proprietary schemas
  in this repo.

## Near-Term Consolidation Direction

For two interns, consolidate around one shared harness with 3-4 clear nodes:

```text
agent host
-> MCP tools
-> evaluator node
-> deterministic gates
```

The next useful node is an evaluator that can inspect:

- original user prompt
- tool selected
- tool input
- tool output
- final answer

and return a structured pass/fail result with reasons.
