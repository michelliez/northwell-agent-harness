# Test suite

Tests are grouped by the subsystem whose behavior they verify:

- `agent_host/`: graph routing, conversations, intent, budgets, and answer safety
- `api/`: FastAPI request/response behavior and the LangGraph adapter
- `evals/`: evaluation runner behavior
- `policy/`: deterministic policy gates and adversarial policy cases
- `retrieval/`: indexing, search, evaluation, and generated-query tooling
- `sql/`: planning, compilation, validation, cost controls, and execution boundaries
- `trace/`: trace records and trace-contract checks
- `fixtures/`: shared test data
- `reports/`: test and red-team reports, not executable test modules

Run everything with `uv run pytest tests`. Run one subsystem by passing its directory,
for example `uv run pytest tests/sql`.
