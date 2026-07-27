# PRD: Stateful Analytics Agent Harness

## Product Goal

Build an observable, policy-gated assistant that helps analysts understand
approved Clarity documentation and draft safe, schema-grounded BigQuery SQL.
The harness must expose and test the path taken—not merely produce plausible
answers—before any production data integration is considered.

## Users

- Data scientists exploring available tables and columns.
- Analysts defining aggregates, cohorts, trends, and metrics.
- Engineers evaluating prompts, models, retrieval, policy, and SQL controls.

The current CLI is a development interface. A user-facing MVP may later use
Streamlit or another thin UI over the same graph API.

## In Scope

- Deterministic input and final-output policy screening.
- Typed intent classification and explicit workflow routing.
- Bounded clarification with stateful interrupt/resume.
- Local indexing and retrieval over approved HTML documentation.
- Evidence-grounded documentation answers with citations.
- Evidence-backed table and column snapshots for SQL planning.
- Safe aggregate SQL drafting and deterministic SQLGlot validation.
- Bounded SQL repair cycles.
- Privacy-aware traces and deterministic evaluation.
- Explicit disabled boundaries for future BigQuery integration.

## Out of Scope for the Current Stage

- Production Epic or BigQuery connectivity.
- SQL execution, dry-run, or cost approval.
- Patient-specific or row-level answers.
- Production authentication, authorization, or OAuth.
- PHI/PII-safe processing of query results.
- Clinical advice or decision support.
- Autonomous writes or destructive operations.
- Durable cross-process conversation storage.
- A fabricated catalog standing in for real schemas.

## Required Request Flow

```text
user input
  -> deterministic input policy
  -> intent classification
  -> refusal, clarification, general answer, or retrieval
  -> retrieved-context gate
  -> documentation answer or evidence-backed query plan
  -> plan safety
  -> SQL generation
  -> deterministic SQL validation
  -> bounded repair or execution-disabled response
  -> result safety, citations, final answer, follow-up state
```

Every new user turn, including clarification, must pass through input policy.
Model output may recommend a route but cannot authorize retrieval or execution.

## Functional Requirements

| ID | Requirement |
|---|---|
| FR1 | Blocked input stops before model and retrieval activity. |
| FR2 | Intent output is typed, confidence-bounded, and mapped to a host-owned route. |
| FR3 | Unknown or low-confidence requests interrupt for bounded clarification. |
| FR4 | Retrieval reads only the configured approved local index and obeys `ExecutionBudget`. |
| FR5 | Documentation answers cite supporting retrieved chunks and admit insufficient evidence. |
| FR6 | SQL planning accepts only an explicit `SchemaSnapshot` derived from evidence. |
| FR7 | Unknown column safety or insufficient schema evidence stops SQL generation. |
| FR8 | SQL generation is limited to read-only aggregate drafts over approved schema. |
| FR9 | SQLGlot validation independently parses tables and columns and fails closed. |
| FR10 | Repairable SQL failures cycle only up to `ExecutionBudget.max_sql_repairs`. |
| FR11 | Valid SQL is labelled as a non-executed draft; no graph route can execute it. |
| FR12 | Final content is screened before return. |
| FR13 | Each request produces a privacy-aware trace with observable routing decisions. |
| FR14 | Thread IDs support in-process state and clarification resume. |
| FR15 | Generated indexes, traces, reports, and views live under ignored `.local/`. |

## Safety Requirements

- Deny overrides allow when deterministic policy findings conflict.
- Refusal intents include patient-specific requests, policy manipulation, and
  unsupported or destructive SQL.
- Retrieved documentation is treated as untrusted data, never instructions.
- Sensitive columns are not projected; identifier use is structurally limited.
- SQL validation never implies authorization, cost approval, or disclosure
  approval.
- Operational failures do not broaden capability.
- Credentials, proprietary documentation, schemas, traces, and real results
  must not be committed.

These controls are development safeguards, not proof of HIPAA compliance or
production authorization.

## State and Extensibility

The graph state contains the question and history, intent decision, permissions,
retrieved chunks, schema snapshot, query plan, SQL and validation status,
citations, answer, bounded-cycle counters, and trace metadata.

Nodes return partial state updates. Conditional edges own routing. Domain logic
belongs in `policy/`, `retrieval/`, and `sql/`; graph nodes coordinate it. New
external systems should implement a narrow domain adapter rather than introduce
generic transport or registry layers prematurely.

## Future BigQuery Release Gates

BigQuery integration may begin only when all of the following have explicit
interfaces and tests:

1. Authoritative schema metadata is resolved into `SchemaSnapshot`.
2. Authenticated identity, role, purpose, and resource scope are available.
3. Dry-run is mandatory before execution.
4. Deterministic byte/cost limits and approval behavior are defined.
5. Execution credentials are read-only and independently scoped.
6. Query timeout and cancellation are enforced.
7. Results pass PHI/PII, identifier, and small-cell screening.
8. Traces record authorization, dry-run, cost, execution, and result-safety
   decisions without exposing sensitive content.

## Acceptance Criteria

- The graph compiles with an in-memory checkpointer.
- General, refusal, documentation, clarification/resume, SQL, and repair routes
  have deterministic routing tests.
- Separate thread IDs do not share state.
- Retrieval bounds and empty-context behavior are tested.
- SQL tests use explicit synthetic `SchemaSnapshot` fixtures rather than a
  global catalog.
- Destructive, multi-statement, wildcard, unknown-schema, sensitive-column,
  and row-level SQL are rejected.
- BigQuery adapter functions fail closed and are not called by the graph.
- Full pytest, Ruff, Pyright, package build, CLI help, and local index/search
  smoke checks pass.

## Success Measures

- Evaluation suites report policy, routing, retrieval, citation, and SQL
  validation failures separately.
- Known fixture questions retrieve the intended documentation.
- Unsupported schema claims and unsafe-to-safe routes remain zero in release
  evaluation sets.
- A contributor can trace the active pipeline without starting local services
  or understanding an obsolete mock architecture.

## Open Decisions

- Which authoritative BigQuery/governance source supplies column sensitivity?
- What authenticated identity and authorization service will the MVP use?
- What dry-run byte and cost thresholds require human approval?
- Which result-suppression rules apply to small cohorts and identifiers?
- When does the UI require durable checkpoint storage rather than memory?
