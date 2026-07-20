# Repository Context

This is the entrypoint for contributors and coding agents working in the agent
harness repository. Read this file first, then load only the canonical document
needed for the task.

## Purpose And Safety

The project evaluates observable, policy-gated agent workflows before any
production Epic or BigQuery integration. It currently uses localhost services,
synthetic fixtures, and approved local documentation.

Never commit credentials, PHI, proprietary schemas, production prompts,
sensitive traces, or real data extracts. Passing this repository's tests does
not establish HIPAA compliance, production authorization, or complete
prompt-injection resistance.

## Canonical Context

- [`README.md`](README.md): setup, commands, package map, and short architecture
  overview.
- [`docs/PRD.md`](docs/PRD.md): product requirements, scope, contracts, and
  acceptance criteria.
- [`docs/RAG.md`](docs/RAG.md): retrieval design, implementation phases, risks,
  and definition of done.
- Small ADRs under `docs/adr/`, if present: accepted decisions and their
  rationale.

Other files under `docs/`, generated graph reports, and test reports are
supporting evidence rather than current instructions. Do not load them by
default or treat them as authoritative when they conflict with the files above.

## Non-Negotiable Invariants

- Evaluate observable probabilistic decisions; enforce hard boundaries with
  deterministic code.
- The host owns execution authority. Model output, intent labels, confidence,
  and MCP discovery do not grant permission.
- Policy-blocked input must stop before intent, model, retrieval, or tool calls.
- Unknown, low-confidence, refused, malformed, or failed intent results must
  fail closed before workflow tools run.
- `agent_host/tool_registry.py` owns executable tool contracts and route scope.
  `agent_host/budget.py` owns execution limits.
- Screen untrusted tool content before model reuse and screen final output
  before returning it.
- Preserve an auditable trace for every stop path and executed action without
  exposing sensitive content by default.

The full behavioral and acceptance contracts live in `docs/PRD.md`; do not
duplicate them here.

## Repository Map

```text
src/
  agent_host/     request lifecycle, authority, budgets, workflows, traces
  policy/         deterministic policy and content screening
  retrieval/      indexing, retrieval client/contracts, retrieval MCP server
  mcp_servers/    intent plus temporary mock catalog/SQL services
  evals/          deterministic evaluation code
  trace_viewer/   trace inspection and rendering
tests/            deterministic unit and integration tests
evals/            versioned synthetic evaluation cases
docs/             canonical specifications and noncanonical references
var/              ignored generated runtime state
```

The fake data catalog and its SQL workflow are temporary fixtures. Do not build
new architecture around their fabricated schemas. Retrieval-backed schema
evidence and SQL behavior are specified in `docs/RAG.md` and `docs/PRD.md`.

## Working Rules

- Keep changes small and reviewable; avoid broad rewrites unrelated to the
  requested behavior.
- Put code in the package that owns the responsibility. Do not add a project-
  name wrapper beneath `src/`.
- Keep workflows explicit under `agent_host/workflows/`; shared mechanics
  belong in focused host modules.
- Add or update tests whenever behavior, routing, policy, contracts, or limits
  change.
- Use synthetic fixtures only. Real schemas and documentation must not be
  checked in without explicit approval and an appropriate data boundary.
- Update one canonical document when a contract changes, then link to it from
  other documents instead of copying the text.
- Treat generated files under `logs/`, `var/`, `dist/`, `.pytest_cache/`, and
  `graphify-out/` as artifacts, not source.

## Commands

Setup:

```powershell
uv sync
Copy-Item .env.example .env
```

Full local verification:

```powershell
uv run ruff format --check src tests
uv run ruff check src tests
uv run pyright
uv run pytest
```

Apply Python formatting and safe lint fixes:

```powershell
uv run ruff format src tests
uv run ruff check src tests --fix
```

Run one test:

```powershell
uv run pytest tests/test_tool_registry.py -q
```

Use `uv run --no-editable` for installed project commands. Model-backed agent
and evaluation commands may incur API usage; run them only when approved. The
complete process commands are kept in `README.md`.

## Change Checklist

Before handing off a code change:

1. Confirm the implementation preserves the invariants above.
2. Update focused tests and any canonical contract that actually changed.
3. Run formatting, lint, type checks, and the relevant tests.
4. Run the complete test suite for cross-cutting changes.
5. Report what changed, what was verified, and what remains intentionally
   temporary or unimplemented.
