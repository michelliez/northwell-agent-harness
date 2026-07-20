# Anthropic Agent-Architecture Audit

**Audit date:** July 16, 2026  
**Scope:** Current checked-out implementation of the synthetic harness. This
document intentionally does not modify the uncommitted design-audit draft in
`PRD.md`.

## Executive conclusion

The harness is best described as a **routed workflow with one bounded,
tool-using agent loop**, not as a multi-agent system. That is a sound choice.
Anthropic recommends starting with the simplest composable workflow that fits
the task, using routing for distinct request categories and adding complexity
only when it demonstrably improves outcomes. The policy -> intent -> scoped
tool loop is therefore a sensible baseline for a synthetic proof of concept.

The strongest implemented choices are:

- A deterministic policy check runs before the classifier, model, or catalog.
- The intent classifier emits typed, forced tool output and is normalized by
  deterministic contract code.
- The host, rather than the model, owns tool visibility and rechecks a
  requested tool name immediately before execution.
- SQL generation is a fixed workflow with a separate deterministic AST
  validator, not an open-ended SQL-writing agent.
- The repository has meaningful unit and adversarial test coverage; the
  checked-out suite passed **510 tests** during this audit.

It is not yet a defensible production data-access architecture. The largest
gaps are server-side authorization, comprehensive treatment of untrusted tool
content, egress/result controls, trace redaction, a complete evaluation
contract, and current live-model evidence. The new surface-aware screen is a
POC defense-in-depth layer, not a substitute for those controls. These are not
reasons to abandon the POC; they are the boundary between a safe learning
harness and a governed healthcare-data system.

## How to read the markers

Anthropic publishes useful engineering guidance and research, not a universal
certification standard. The labels below compare the implementation to those
published patterns and to the repository's own PRD.

| Marker | Meaning |
| --- | --- |
| **Aligned** | The implementation follows a useful published pattern for its current scope. |
| **Deliberate POC departure** | The repository explicitly limits this capability to a local, synthetic, non-production demonstration. This is acceptable only inside the stated boundary. |
| **Implementation gap** | The code or evidence is incomplete, internally inconsistent, or lacks a documented rationale. Do not describe it as an intentional safety control. |
| **PRD/documentation drift** | The implementation, PRD, tests, or explanatory material disagree. This matters because new team members will use the docs to form their mental model. |

## Baseline used for comparison

- [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents): distinguish workflows from agents, keep designs simple, use routing and programmatic gates, and test sandboxed agent loops.
- [Trustworthy agents in practice](https://www.anthropic.com/research/trustworthy-agents): assess the model, harness, tools, and environment together; retain human control; use layered defenses against prompt injection.
- [How tool use works](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works), [Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls), and [Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use): the application executes client tools, controls the loop, handles stop states and tool errors, and validates tool inputs.
- [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents) and [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents): expose a small, clear, non-overlapping toolset; return high-signal, bounded results; treat context as finite.
- [Mitigating prompt injections](https://www.anthropic.com/research/prompt-injection-defenses) and [Constitutional Classifiers](https://www.anthropic.com/news/constitutional-classifiers): every untrusted item that enters model context is an attack surface; input and output controls, red teaming, and other complementary defenses are needed because no single layer is sufficient.
- [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents): use transcripts and outcomes, repeated trials, code/model/human graders where appropriate, and retain regression baselines.

## Current architecture in plain language

```text
Untrusted user prompt
  -> deterministic first-pass user-input policy screen
  -> forced structured intent classifier
  -> one of:
       - clarification/refusal (stop)
       - no-tool general answer
       - fixed SQL workflow: catalog -> generator -> AST validator (stop; no execution)
       - bounded catalog agent loop: screen tool metadata -> model proposes a tool
         -> host executes it -> screen tool result -> model answers
  -> screen final answer before returning it
  -> JSONL trace
```

This maps cleanly to Anthropic's terminology:

| Component | Correct term | Why it matters |
| --- | --- | --- |
| Policy gate | Deterministic workflow node | It evaluates known, reproducible rules; it is not an autonomous agent. |
| Intent classifier | Semantic routing workflow node | It emits a bounded route but cannot access data or execute work. |
| Catalog loop | Agentic loop | The model dynamically decides whether to call an exposed catalog tool after observing results. |
| SQL path | Fixed workflow | The host dictates catalog lookup, generation, and validation order. Avoiding extra autonomy here is a good design decision. |
| MCP servers | Tool integration boundary | MCP standardizes a connection; it does not grant identity, authorization, or data safety. |
| Agent host | Harness / execution authority | The host must remain the component that decides whether a model proposal may cause an action. |

## Node-by-node comparison

| Node | What is aligned | Departures and needed interpretation | Current POC position |
| --- | --- | --- | --- |
| Policy gate | Runs first; deterministic checks are separate from model routing; block wins over allow. The host now applies a reusable surface-aware screen at model-context crossings. | **Implementation gap:** the checks remain lexical/structural defense in depth, not authorization or an end-to-end security boundary. Direct MCP callers can still bypass the host. | Useful first-pass screen plus context checks, not authorization. |
| Intent classifier | Forced typed tool output; closed intent/action vocabulary; fail-safe on malformed/unavailable results; refusal is now host-enforced. | **Implementation gap:** model-reported confidence is uncalibrated; extra fields are silently accepted by Pydantic; risk flags have no controlled taxonomy. | Good router for a synthetic POC, not an independent safety system. |
| Host and agent loop | Host derives route scope from one pinned tool-contract registry, screens metadata/results/final answers, validates tool inputs/results, checks names again before execution, and enforces operational budgets and model stop states. | **Implementation gap:** no egress control, provider-dollar cost enforcement, or production identity propagation. | Stronger POC containment; still not a production security boundary. |
| Data catalog MCP | Small, static tool surface; host-mediated calls use a configured service token and server-side token verification. | **Deliberate POC departure:** the token is coarse service authentication, not principal/resource authorization or mTLS/OAuth. | Suitable only for checked-in dummy data. |
| SQL generation and validation | Fixed orchestration; forced structured candidate; AST-based read-only/schema/identifier checks; no query execution. | **Deliberate POC departure:** no BigQuery execution or dry run. **Implementation gap:** no result suppression/minimum-cell control, data-cost limit, or server-side authorization. | Strong mock SQL safety experiment, not a governed analytics path. |
| Trace logging | Per-run ordered JSONL records make trajectory checks possible; metadata mode centrally redacts content and retains sizes/digests. | **Implementation gap:** retention, access control, completeness, latency, token, and cost fields still need production treatment; debug mode must remain local-only. | Useful evaluation trace, not a production audit log. |
| Evaluation harness | Deterministic trace assertions, an intent corpus of 90 synthetic cases, and repeated direct intent trials. | **PRD/documentation drift:** no normalized `RunTrace`, replay path, semantic judge/human calibration, host-level repeated trials, baseline comparison, or release thresholds. Only 26 end-to-end JSONL cases are present. | A valuable start, not yet the PRD's evaluation foundation. |

## Explicit deliberate POC departures

Only the following are treated as deliberate. Each is grounded in the PRD or
repository documentation; none should be presented as a production safeguard.

| ID | Deliberate departure | Evidence and rationale | Required boundary |
| --- | --- | --- | --- |
| **DP-01** | Synthetic data only | The PRD excludes Northwell/Epic data and requires synthetic or approved metadata. `data_catalog.py` returns checked-in dummy fixtures. | Do not introduce real schemas, prompts, logs, or extracts into this repository. |
| **DP-02** | No SQL execution | The PRD explicitly excludes production BigQuery generation/execution. The SQL workflow returns mock SQL after local validation and has no execution tool. | Keep the path non-executing until a separately approved governed-execution design exists. |
| **DP-03** | No principal, consent, role, or resource authorization | The servers now require a shared local service token, but that token authenticates the host process rather than an end user or resource grant. | Do not expose a server beyond the local synthetic environment or attach any protected resource. |
| **DP-04** | No human approval step | All current actions are read-only and operate over static dummy data. Omitting approval is reasonable at this stage. | Add explicit approval/plan review before any consequential, write, external-communication, or real-data action. |
| **DP-05** | No multi-agent design or durable memory | The task is narrow and the host bounds the loop to three rounds. Anthropic advises beginning with simple workflows rather than adding orchestration prematurely. | Reconsider only if evidence shows a longer or genuinely decomposable task needs it. |
| **DP-06** | Static, tiny catalog results | Candidate schema loading is now capped by a host-owned fan-out budget and remains limited to dummy data. | Add authorization-aware ranking/filtering and stronger result minimization before a real catalog is used. |

The following omissions are sometimes described informally as "POC-only," but
they are not sufficiently documented as an intentional design decision and
should be treated as **gaps**: comprehensive output/result controls beyond the
basic surface-aware screen, trace redaction, model-confidence calibration, and
the missing PRD evaluation contracts.

## Detailed findings

### 1. Policy gate and surface screen

**Aligned.** `answer_question()` invokes `blocked_response_if_needed()` before
the intent MCP, model, or catalog (`agent_host/agent.py:28-47` and `414-436`).
The gate is modular, normalizes input, and uses deny-overrides consolidation
(`policy/gates.py:21-47`, `policy/consolidate.py:8-23`). This is a good use of
the programmatic gates Anthropic places between workflow steps.

**Current POC wording — it is a deterministic first-pass screen, not a
security boundary.** The user-input policy still runs before intent and
catalog access. A reusable surface-aware screen now checks MCP tool metadata,
tool results, and final answers before those values cross into the next model
step or back to the caller. Anthropic's prompt-injection research treats all
untrusted content entering a model's context as an attack surface, and its
classifier work uses both input and output screening. These checks improve
coverage but remain a cheap, traceable defense-in-depth layer—not
prompt-injection resistance or authorization.

**Fixed regression — aggregate language no longer bypasses row-level
detection.** `check_row_level_request()` now checks row-level verbs and
objects before applying the aggregate exemption. The following request is
blocked before intent or model access, while aggregate-only analytics remain
allowed:

```text
Count visits and list the individual records.
```

The regression test asserts the row-level block and the absence of downstream
model/tool events. This is still a first-pass screen; production PHI control
requires identity-aware authorization and server-side enforcement.

**Implementation gap — the result type is overloaded.** Both an explicit
workflow route and "no deterministic verdict" return `allowed=true`
(`policy/result.py:27-45`). The trace distinction is helpful, but a future
caller can easily treat both as authorization. Replace the boolean with a
decision enum such as `block`, `route`, and `no_verdict`; never use policy or
intent labels as access authorization.

### 2. Intent classifier

**Aligned.** The classifier forces one named `emit_intent` tool call, validates
the result, and deterministically resolves incoherent intent/action pairs
(`mcp_servers/intent.py:147-190`, `193-268`). `classify_request()` now blocks
refusal intents before catalog access (`agent_host/agent.py:114-151`). This
implements the useful separation between semantic routing and action
authority.

**Implementation gap — confidence is not calibration.** `MIN_CONFIDENCE=0.70`
is a fixed threshold applied to model self-reported confidence
(`mcp_servers/intent.py:41-49`). There is no held-out reliability study that
shows a reported 0.70 corresponds to an acceptable error rate, especially for
unsafe-to-safe routes. Anthropic's evaluation guidance calls for repeated
trials and human calibration where semantic assessment is needed. Treat the
threshold as a conservative experiment, not a quantified safety margin.

**Implementation gap — strict runtime validation is incomplete.** The tool
schema declares `additionalProperties=false`, but the Pydantic models do not
set `extra="forbid"`; Pydantic therefore ignores unexpected fields. Add strict
runtime model configuration and, where supported, Anthropic's `strict: true`
tool property for model-to-host tool calls.

**Implementation gap — response semantics disagree with control flow.** A
valid `unknown`/clarification response and an unauthorized-tool response stop
the workflow but inherit `AskResponse.allowed=True`
(`agent_host/schemas.py:9-18`, `agent_host/agent.py:135-150`, `599-609`).
Operational failures correctly use `allowed=False`. A typed outcome such as
`clarified`, `refused`, `blocked`, `completed`, or `failed` would accurately
represent what happened and improve evaluation metrics.

**Implementation gap — same-model correlated failure.** The router and acting
model use the same configured Claude model/API settings. That is economical
for a POC, but it is not independent defense in depth. Anthropic's workflow
examples note the value of a separate guardrail call; its trustworthy-agent
guidance also emphasizes layers rather than a single model judgment. Document
the cost/latency tradeoff or use an independently evaluated high-risk guard
before treating intent as a safety layer.

### 3. Host, tool boundary, and agent loop

**Aligned.** The host owns a single `ToolContract` registry, derives a route's
tools from it, exposes canonical definitions to the model, and rejects a tool
name outside that subset just before execution. This follows
Anthropic's core tool-use contract: the model proposes a structured request;
the application executes only what it permits.

**Current POC containment.** A per-run budget now limits total/per-tool calls,
input/result/context bytes, schema fan-out, wall time, MCP timeouts, and model
output limits. Provider-dollar accounting, egress control, and server-side
authorization remain production prerequisites.

**Current stop-state handling.** `max_tokens` and refusal responses now produce
explicit fail-closed trace events instead of being treated as ordinary answers.
Unknown or malformed model responses remain failure paths. The loop continues
only when it finds a validated `ToolUseBlock` and the budget permits it;
otherwise it treats a response as final only after the explicit stop-state
check. This keeps incomplete/refused responses from becoming successful
completions.

**Current POC contract boundary.** The host now uses one pinned
`ToolContract` registry to derive route scopes, validate live MCP input schemas,
and pass canonical descriptions/schemas to Claude. A missing or drifted
required tool fails closed. Before remote or real-data use, authenticate the
server and enforce resource checks at the server; host pinning is not a
replacement for server-side authorization.

**Partial implementation — tool results are contract-validated and screened
before reuse.** Results are still JSON-serialized into the next model turn,
but the host now applies typed shape checks, byte limits, provenance-free
dummy handling, and the reusable surface screen. Keeping untrusted content
inside `tool_result` blocks is correct API formatting, but it does not
neutralize indirect prompt injection. Add richer provenance/filtering and an
independently evaluated safety classifier for retrieved or third-party content
before real data use.

### 4. Data-catalog MCP node

**Deliberate POC departure.** The catalog is a static, localhost, dummy-data
server (`mcp_servers/data_catalog.py:26-102`, `244-245`). It has no user
identity or authorization check. This is acceptable only because DP-01 through
DP-03 apply.

**Implementation gap before real metadata.** The host's tool filtering is not
authorization. A local process can call any MCP endpoint directly, and a
future remote caller would not carry a role, purpose, consent, tenant, or
resource scope into the server. Use server-side identity-aware authorization
and minimum-necessary resource filtering, not a user prompt or an intent label.

**Design note.** The four tools are understandable for a small POC, but
`search_docs`/`get_table_info` overlap with `search_tables`/`get_table_schema`,
and tool names are not service-namespaced. Anthropic recommends a small,
non-overlapping tool surface and clear namespacing as toolsets grow. This is a
low-priority quality issue now; it will become important once multiple MCP
servers are exposed to one model.

### 5. SQL generation and validation path

**Aligned.** The SQL path is deliberately fixed: catalog discovery, structured
generation, deterministic validation, then return mock SQL without executing
it (`agent_host/agent.py:240-334`). The generator has a forced structured
output (`mcp_servers/sql_generation.py:75-142`) and the validator parses an
AST, validates the schema, blocks non-read-only operations, rejects sensitive
columns, and limits identifier use (`mcp_servers/sql_validation.py`). This is
much safer and easier to evaluate than giving the main agent an arbitrary SQL
execution tool.

**Deliberate POC departure.** There is no BigQuery connection or execution.
Retain that boundary; it is consistent with the PRD's non-goals.

**Implementation gap before any query execution — aggregation is not release
safety.** The validator permits ordinary grouped counts such as:

```sql
SELECT encounter_date, COUNT(*) AS visit_count
FROM encounters
GROUP BY encounter_date
```

This passed the current validator during the audit. It has no minimum-cell
suppression, output-cardinality rule, date/generalization rule, differential
privacy mechanism, dry run, query-cost cap, or result-level policy check.
Aggregate-only SQL prevents a class of leaks; it does not by itself prevent
small-cell disclosure or expensive queries.

**Implementation gap — broad schema loading will not scale safely.**
`run_safe_sql_workflow()` fetches the schema of every table returned by
`search_tables` (`agent_host/agent.py:250-369`). That is harmless for three
dummy tables, but it conflicts with Anthropic's guidance to use minimal,
high-signal, just-in-time context. Before a real catalog, scope candidate
tables by authorization and rank/select them before loading schemas.

### 6. Trace and privacy node

**Aligned.** A per-run JSONL trace gives deterministic graders observable
events rather than hidden chain-of-thought. That is a good foundation for
trajectory evaluation.

**Fixed regression — trace content is redacted centrally.**
`TRACE_CONTENT_MODE=metadata` is now the default. `TraceLogger` redacts
request, model, tool, SQL, result, and answer fields before serialization while
retaining event structure, sizes, item counts, and keyed digests:

`TRACE_CONTENT_MODE=debug` remains an explicit local-development escape hatch;
it must not be enabled for sensitive content. Retention/access policy and
trace-reader audit are still production requirements.

**PRD delivery gap.** The trace does not capture scenario ID, model/prompt/
tool/policy/evaluator versions, latency, token usage, cost, redaction metadata,
or a completeness state. Those are explicit PRD requirements. Add a versioned
normalized trace event schema before using results as a release baseline.

### 7. Evaluation node

**Aligned.** The repository has a useful deterministic starting point:

- 90 direct intent cases across three 30-case rounds;
- 5 smoke and 21 red-team end-to-end JSONL cases;
- trace-order and tool-call assertions; and
- optional repeated trials for the direct intent suite.

The audit command `uv run --no-editable pytest -q` passed 510 tests.

**PRD delivery gap — the current harness is not yet the specified evaluation
foundation.** The PRD requires replay, `ScenarioSpec`, normalized `RunTrace`,
typed `EvalResult`, tool-argument constraints, severity, semantic judging with
human calibration, repeated live runs, baselines, release thresholds, and
Markdown reporting. Current code has live execution plus direct intent
repetitions, but no stored-trace replay evaluator, no model/human grader, no
baseline comparison, and no host-level reliability calculation. The 26
end-to-end cases are also short of the PRD's 30-scenario target.

**Implementation gap — grade outcomes as well as exact trajectories.**
`EvaluationCase` is useful but narrowly checks expected tool-name sequences,
string claims, and a single broad event order (`evals/assertions.py`).
Anthropic cautions that rigid trajectory checks can reject valid strategies;
pair indispensable hard constraints with outcome/grounding graders and allow
multiple valid safe paths. Continue to make policy violations non-negotiable.

**PRD/documentation drift — the intent corpus and current router disagree on
SQL.** The current classifier prompt treats a safe aggregate SQL request such
as "Write SQL to count appointments by status" as `safe_sql_generation`
(`mcp_servers/intent.py:104-106`, `131-134`). Yet several Round 1 intent
cases expect aggregate SQL requests to be `unsupported_sql_request`
(`evals/intent.jsonl:27-30`). Resolve the product decision and version the
corpus before treating a future report as a current-model baseline.

**PRD/documentation drift — refusal enforcement is now implemented.**
`README.md`, `CLAUDE.md`, and `docs/SECURITY_ARCHITECTURE_STRATEGY.md` still
say the host does not enforce the intent classifier's `refuse` action. Current
code does enforce it in `classify_request()` and the test suite verifies it.
Correct these documents before onboarding the team; otherwise the written
baseline misstates a major safety boundary.

## Priority order for design decisions

This is a decision sequence, not an instruction to turn the POC into a
production system immediately.

1. **Make the current POC truthful and measurable.** Correct documentation
   drift; turn `allowed` into a typed outcome; redact tool inputs/results and
   final answers; record configuration/version/completeness metadata; handle
   model stop states and tool errors explicitly.
2. **Close the evaluator/PRD gap before using it as a release gate.** Add
   replay, normalized trace contracts, scenario ownership/versioning,
   argument/outcome/grounding checks, host-level repeated trials, and a stored
   baseline. Add semantic judging only alongside a human-calibrated rubric.
3. **Keep the SQL path non-executing until the data-control design exists.**
   An execution proposal needs identity-aware server authorization, approved
   tables/columns, dry-run and cost bounds, small-cell/result controls,
   output validation, audit, and an approval policy.
4. **Before any real content reaches a model, treat every model-context
   crossing as security-relevant.** Authenticate/pin MCP servers, minimize
   exposed tools and schemas, validate/bound tool results, screen untrusted
   content, and add an egress control appropriate to the data class. The POC's
   surface-aware screen is defense in depth, not authorization.
5. **Do not add multi-agent orchestration merely to appear agentic.** The
   existing fixed routing/workflow split is more explainable and likely safer
   for the stated goal. Require evaluation evidence before increasing autonomy.

## Defensible language for the team

> This is a synthetic agent-harness POC. It uses a deterministic first-pass
> policy veto, a structured semantic router, and host-owned tool scopes. The
> model can propose a catalog action, but it cannot grant itself access or
> execute SQL. The design follows Anthropic's simple-workflow, typed-tool, and
> trace-evaluation patterns. It deliberately excludes real data, execution,
> identity, authorization, and production monitoring; those omissions are
> explicit blockers for any governed healthcare-data use.

Avoid claims such as "MCP makes the tools secure," "the policy gate prevents
prompt injection," "an allowed route authorizes access," or "passing the unit
tests demonstrates production safety."
