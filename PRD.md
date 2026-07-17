# PRD: Epic-to-BigQuery Agent Evaluation Harness

## 1. Product Statement

Build a reusable harness that determines whether probabilistic agent nodes made the right decision, used the right tool with the right inputs, and produced a grounded result.

The first proving workflow is safe discovery over synthetic or approved Epic-style metadata. The long-term product is a governed Epic-to-BigQuery assistant for discovery, mapping, query planning, validation, and read-only analytics. The evaluation harness is the first product increment and the release gate for every later agentic capability.

## 2. Problem

Epic-to-BigQuery work contains decisions that are useful but inherently probabilistic: interpreting a request, selecting documentation, choosing a tool, constructing a plan, repairing an error, and explaining a result. Traditional unit tests can verify deterministic code, but they do not show whether an agent took the right path.

A plausible final answer can hide a bad trajectory: the wrong tool, incorrect arguments, unsupported claims, unnecessary calls, or a policy violation. Northwell Data Solutions needs one shared way to expose those failures, compare models or prompts, and prevent regressions before agent workflows reach governed data.

## 3. Users and Organizational Value

Primary users are:

- Agent and application engineers testing prompts, models, tools, and orchestration.
- Data engineers and data scientists validating that an agent understands metadata, mappings, and query intent.
- QA, governance, and product owners reviewing safety, grounding, and release readiness.

The same evaluation contract should support:

- Epic schema and documentation discovery.
- Source-to-target mapping proposals.
- Retrieval and context selection.
- Query planning and SQL generation.
- Data-quality triage and lineage impact analysis.
- Result interpretation and citation.

This shared layer is the main source of organizational leverage: teams add domain scenarios and adapters instead of building a new evaluation system for every agent.

## 4. Goals

The MVP will:

1. Define a provider- and framework-neutral trace contract for observable agent behavior.
2. Run or replay versioned scenarios against an agent.
3. Grade each probabilistic node with deterministic assertions first and a calibrated model judge only when semantic judgment is required.
4. Repeat stochastic cases and report reliability, not a single favorable run.
5. Localize failures to a node, criterion, and supporting trace evidence.
6. Compare model, prompt, tool, and policy versions against a baseline.
7. Use synthetic or explicitly approved metadata only; no PHI is required for the MVP.

## 5. Non-Goals for the MVP

The MVP will not:

- Ingest Northwell Epic data or the full Epic documentation corpus.
- Generate or execute production BigQuery SQL.
- Replace deterministic security, permission, SQL, cost, or result-safety gates.
- Serve as a general observability platform or agent framework.
- Make clinical decisions or evaluate clinical care.
- Use hidden chain-of-thought as an evaluation input.
- Automatically approve a release from an uncalibrated model-judge score.

## 6. Core Design Rule

**Evaluate every observable probabilistic decision; enforce every hard boundary with deterministic code.**

The evaluator may inspect the user request, structured model output, tool choice, tool arguments, tool result, state transition, final answer, and operational metadata. It must not depend on private reasoning traces.

| Decision type | Examples | Required method |
| --- | --- | --- |
| Deterministic | Schema validity, allowed tools, exact arguments, call limits, forbidden fields, policy rules | Code assertion; a model judge cannot override it |
| Probabilistic | Intent, semantic relevance, clarification quality, plan fidelity, grounded explanation | Explicit rubric; model judge where needed; human-calibrated |

## 7. MVP Contracts

### 7.1 `ScenarioSpec`

A versioned scenario must contain:

- Stable ID, version, owner, domain, and risk tags.
- User request and optional follow-up turns.
- Available tool definitions or deterministic mocks.
- Required, optional, and forbidden outcomes.
- Required or forbidden tool calls, argument constraints, and ordering rules.
- Reference facts or approved source excerpts needed for grounding.
- Per-criterion grading method, severity, and pass threshold.

### 7.2 `RunTrace`

A normalized run trace must contain:

- Run ID and scenario ID.
- Model, prompt, tool, policy, and evaluator versions.
- Ordered observable events: model output, route, tool call, tool input, tool result, gate decision, retry, and final answer.
- Latency, tool-call count, token usage, and cost when available.
- Redaction metadata and trace completeness status.

### 7.3 `EvalResult`

Every criterion result must contain:

- `pass`, `fail`, or `abstain`.
- Node, criterion, severity, score when applicable, and evaluator type.
- Trace evidence supporting the decision.
- Human-readable failure reason and expected behavior.
- Evaluator and rubric version.

The aggregate result must include per-node results, overall task success, critical-policy status, repeated-run reliability, and change from baseline.

## 8. Evaluation Dimensions

| Dimension | Question | MVP grading |
| --- | --- | --- |
| Routing and intent | Did the agent choose the correct next step or ask for needed clarification? | Expected route plus semantic rubric for borderline intent |
| Tool selection | Did it call the required tool and avoid irrelevant or forbidden tools? | Deterministic call assertions |
| Tool input | Did it pass the correct entity, table, filter, and other arguments? | Exact, set, pattern, and predicate checks; semantic judge only when needed |
| Trajectory | Did it satisfy required milestones, respect ordering, and avoid prohibited actions? | Deterministic milestone, ordering, retry, and minefield checks |
| Grounding | Are claims supported by approved tool output or reference context? | Fact and citation checks plus a structured grounding rubric |
| Outcome | Did the run satisfy the user's request at the correct level of detail? | Expected state or facts plus a structured task rubric |
| Safety | Did it avoid PHI, destructive actions, permission changes, and policy bypasses? | Deterministic checks with critical severity |
| Reliability and efficiency | Does it behave consistently without excessive calls, latency, or cost? | Repeated-run and operational metrics |

## 9. Evaluation Workflow

```text
Versioned scenario
-> run the agent or replay a stored trace N times
-> normalize events into RunTrace
-> run deterministic graders
-> run semantic judge only for unresolved criteria
-> aggregate node, run, and repeated-trial results
-> compare with baseline and release thresholds
-> emit JSON and concise Markdown reports
```

The current Anthropic-plus-MCP loop is the reference system under test. The evaluator must consume a normalized trace so future LangGraph, ADK, custom, or other agent implementations can use the same scenarios and graders through adapters.

## 10. Functional Requirements

| ID | Requirement | Acceptance condition |
| --- | --- | --- |
| FR1 | Run and replay | The same scenario can evaluate a live run or a stored JSONL trace. |
| FR2 | Trace normalization | An adapter maps source events to `RunTrace`; missing required events fail explicitly. |
| FR3 | Deterministic graders | Support route, required/forbidden tool, argument, ordering, call-count, policy, fact, citation, and budget assertions. |
| FR4 | Semantic judge | Return a typed result with rubric scores, cited trace evidence, confidence, and `abstain`; never override a hard failure. |
| FR5 | Repeated trials | Configure trial count and report pass rate, all-trials-pass reliability, variance, and critical failures across trials. |
| FR6 | Regression comparison | Compare model, prompt, tool, and policy versions by scenario tag, node, criterion, and severity. |
| FR7 | Actionable reports | Emit machine-readable JSON, concise Markdown, and a nonzero CLI exit code when a release threshold fails. |
| FR8 | Privacy | Default to synthetic fixtures, redact configured fields, exclude secrets, and make raw prompt/tool-result logging opt-in. |
| FR9 | Extensibility | Add a domain through scenarios, mocks, and an optional trace adapter without changing core grading logic. |

## 11. Initial Scenario Suite

The MVP suite will contain at least 30 versioned synthetic scenarios across these failure classes:

- Correct tool use and correct no-tool behavior.
- Wrong tool, missing tool, and unnecessary tool calls.
- Wrong, incomplete, hallucinated, or broadened tool arguments.
- Ambiguity that should trigger clarification instead of guessing.
- Unsupported claims, incorrect citations, or ignored tool results.
- Unsafe requests, prompt injection, prohibited data, or forbidden actions.
- Excessive retries, calls, latency, or cost.

The dummy Epic-style data catalog is the primary reference workflow. At least one second dummy tool workflow will prove that the evaluator is not coupled to the catalog domain.

## 12. MVP Acceptance Criteria

The MVP is complete when:

- At least 30 scenarios run through both live and replay paths.
- Seeded deterministic failure fixtures are detected with 100% recall.
- Every failed criterion names the node, expected behavior, actual evidence, and severity.
- Stochastic release scenarios run at least five trials and report both average success and all-trials-pass reliability.
- No critical safety scenario passes if any trial violates its hard boundary.
- The semantic judge reaches at least 85% agreement with human labels on a held-out calibration set and has no critical false negatives; otherwise it remains advisory.
- A baseline comparison identifies regressions by model, prompt, tool, or policy version.
- Reports include configuration, token, latency, tool-call, and cost metadata when available.
- No PHI, credentials, proprietary schema exports, or unapproved Northwell data are stored in the repository or evaluation artifacts.

## 13. Product Roadmap

| Phase | Product capability | Probabilistic nodes under evaluation |
| --- | --- | --- |
| 1. Evaluation foundation - current scope | Scenario registry, trace adapters, graders, repeated trials, reports, and regression gates | Tool choice, tool input, tool-result use, and final answer |
| 2. Epic metadata discovery | Retrieval over approved Epic documentation and schema metadata | Intent, clarification, retrieval query, context selection, and cited answer |
| 3. BigQuery planning | Structured query plan, deterministic SQL generation or constrained generation, static validation, and dry run | Plan construction, repair choice, and result interpretation |
| 4. Governed execution | Approved read-only queries, cost controls, result safety, audit, and human approval where required | Follow-up routing and safe summarization |

Every new probabilistic node must define its inputs, observable outputs, failure taxonomy, scenarios, and release thresholds before promotion. Deterministic gates are tested separately and cannot be bypassed by agent or evaluator output.

## 14. Build Order

1. Define `ScenarioSpec`, `RunTrace`, and `EvalResult` with Pydantic.
2. Adapt the existing JSONL trace and add offline replay.
3. Implement deterministic graders and seeded failure fixtures.
4. Add the structured semantic judge and human calibration set.
5. Add repeated execution, aggregation, baseline comparison, and CLI/Markdown reporting.
6. Complete the 30-scenario data-catalog suite and portability demonstration.

## 15. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Model-judge bias or inconsistency | Use deterministic graders first, blind model identity where possible, require trace evidence, allow abstention, and calibrate against held-out human labels. |
| Overfitting to a small suite | Version scenarios, retain a holdout set, add adversarial variants, and report performance by failure class. |
| Framework coupling | Normalize observable events and keep provider-specific logic in adapters. |
| False confidence from average scores | Report critical failures and repeated-trial reliability; one hard-boundary failure blocks release. |
| Sensitive data in traces | Use synthetic fixtures by default, redact before persistence, and keep raw logging disabled unless explicitly approved. |
| Evaluation becomes a dashboard without release value | Require thresholds, nonzero CI exit codes, regression ownership, and a failure reason that identifies the responsible node. |

## 16. Open Decisions

- Which approved Epic documentation subset will be the first Phase 2 corpus?
- Which approved model will serve as the semantic judge, and who owns calibration labels?
- Where should enterprise traces, baselines, and reports live outside this spike repository?
- Which risk-tier thresholds and approvers will govern later BigQuery execution workflows?

## 17. Research Basis

The design uses a small set of directly relevant publications:

- Fine-grained trajectory evaluation is necessary because final success alone does not localize agent failures: Ma et al., [AgentBoard: An Analytical Evaluation Board of Multi-turn LLM Agents](https://proceedings.neurips.cc/paper_files/paper/2024/hash/877b40688e330a0e2a3fc24084208dfa-Abstract-Datasets_and_Benchmarks_Track.html), NeurIPS 2024.
- Stateful tool evaluation should score intermediate milestones and prohibited actions across an arbitrary trajectory: Lu et al., [ToolSandbox: A Stateful, Conversational, Interactive Evaluation Benchmark for LLM Tool Use Capabilities](https://aclanthology.org/2025.findings-naacl.65/), Findings of NAACL 2025, doi:10.18653/v1/2025.findings-naacl.65.
- Agent reliability requires repeated trials and policy-aware tool interaction, not only pass@1: Yao et al., [tau-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains](https://proceedings.iclr.cc/paper_files/paper/2025/hash/1b126cc38b8638e07bef37e7b2bb72bf-Abstract-Conference.html), ICLR 2025.
- LLM judges can scale semantic evaluation but exhibit position, verbosity, and self-enhancement biases, so they require controlled rubrics and human calibration: Zheng et al., [Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena](https://proceedings.neurips.cc/paper_files/paper/2023/hash/91f18a1287b398d378ef22505bf41832-Abstract-Datasets_and_Benchmarks.html), NeurIPS 2023.
- Healthcare agents should be evaluated in realistic workflows with attention to tool use and downstream failure points: Mehandru et al., [Evaluating Large Language Models as Agents in the Clinic](https://doi.org/10.1038/s41746-024-01083-y), npj Digital Medicine 7, 84 (2024).
- Healthcare evaluation requires planned human review and adjudication: Tam et al., [A Framework for Human Evaluation of Large Language Models in Healthcare Derived from Literature Review](https://doi.org/10.1038/s41746-024-01258-7), npj Digital Medicine 7, 258 (2024).
- Evaluation and risk measurement should span the system lifecycle: Autio et al., [Artificial Intelligence Risk Management Framework: Generative Artificial Intelligence Profile](https://doi.org/10.6028/NIST.AI.600-1), NIST AI 600-1 (2024).

## 18. Policy and Intent Classifier Design Audit

### 18.1 Audit Scope, Evidence, and Rating Scale

This audit was completed on July 16, 2026 against commit `77a82e4` on the
`michelle` branch. It covers only the policy gate, intent classifier, and the
host controls that make their decisions effective. It does not assess SQL
generation, SQL validation, production Epic connectivity, or BigQuery
execution.

The audit distinguishes architectural patterns from implementation evidence.
An architecture can be industry-aligned while its POC implementation remains
unsuitable for production. “Industry-aligned” below means consistent with
published practices from Anthropic, the MCP project, HHS, and agent-evaluation
literature; it does not mean that one universal agent standard exists.

| Score | Meaning |
| ---: | --- |
| 5 | Directly aligned with a published pattern, implemented, tested, and supported by current evidence. |
| 4 | Strongly aligned and correctly implemented for this synthetic POC; production hardening remains. |
| 3 | Reasonable POC choice with material limitations or incomplete evidence. |
| 2 | Significant departure, ambiguous control semantics, or an uncalibrated safety dependency. |
| 1 | Missing control; acceptable only because this POC uses dummy data and localhost services. |

Scores are design-audit judgments, not certifications or measurements of
HIPAA compliance, jailbreak resistance, or production readiness.

### 18.2 Conceptual Model for Teams New to Agentic Systems

| Concept | General meaning | Meaning in this POC | Important boundary |
| --- | --- | --- | --- |
| Workflow node | One observable step in a predefined code path. It may be deterministic or model-backed. | Policy and intent are both workflow nodes. | A “node” is not automatically an autonomous agent. |
| Agentic node or loop | A model dynamically chooses its next action or tool based on observations. | The bounded catalog loop is agentic because Claude may choose a scoped tool and then react to its result. | The host, not the model, must retain execution authority. |
| Deterministic screen/gate | Code applies explicit, reproducible rules to observable input or output. | The policy gate normalizes user input; the surface screen applies narrower checks to tool metadata, tool results, and final answers. | Deterministic means reproducible, not comprehensive or inherently secure. |
| Semantic router | A probabilistic classifier maps natural language into a bounded route. | The intent MCP uses Claude to emit a typed intent, confidence, risk flags, action, and clarification flag. | Intent is routing metadata, not authorization or factual evidence. |
| MCP | An open protocol for discovering and invoking schema-defined tools and resources. | FastMCP exposes the intent classifier and dummy catalog over HTTP. | MCP standardizes integration; it does not itself grant identity, authorization, sandboxing, or data safety. |
| Host or broker | The trusted application layer that decides which model outputs may cause actions. | `agent.py` runs policy, calls intent, filters tool definitions, checks requested tool names, executes allowed calls, and records traces. | This is the effective POC enforcement point. |
| Authorization | A decision based on authenticated identity, role, purpose, resource, action, and context. | Not implemented. The POC has workflow routing and tool-name scoping only. | A policy or intent label must never be presented as production authorization. |

Anthropic distinguishes predefined **workflows** from **agents** that
dynamically control tool use and recommends beginning with the simplest design
that meets the need. Its routing pattern classifies an input and sends it to a
specialized path, and its prompt-chaining pattern permits programmatic gates
between steps. That is the closest published architectural analogue to this
POC, rather than five independent autonomous agents. See [Anthropic, Building
Effective Agents](https://www.anthropic.com/engineering/building-effective-agents).

### 18.3 Current Control Flow and Trust Boundaries

```text
Untrusted user prompt
  -> host records request.received
  -> deterministic first-pass user-input policy screen
       -> block: allowed=false, record request.blocked, stop
       -> explicit workflow allow: continue to intent
       -> no deterministic verdict: continue to intent
  -> intent MCP
       -> force exactly one emit_intent tool call
       -> validate IntentResult
       -> normalize intent/action contract
       -> refuse or uncertain: stop before catalog
       -> safe route: return routing metadata to host
  -> host derives allowed tool names from its own ToolContract registry
  -> surface-screen tool metadata, then show only that subset to the main model
  -> host checks every requested tool name against the same subset
  -> allowed MCP tool executes against dummy data
  -> surface-screen tool result before the next model step
  -> model produces final answer
  -> surface-screen final answer before returning it
```

Control ownership is deliberately split:

| Decision | Owner | Current semantics |
| --- | --- | --- |
| Obvious prohibited content | Deterministic policy gate | A veto. Any block wins over an allow finding. |
| Safe-workflow recognition | Deterministic policy gate | A routing hint that permits semantic classification; not a data-access grant. |
| No lexical match | Deterministic policy gate | `allowed=true` with `reason="no_deterministic_verdict"`; operationally means “escalate to intent.” |
| Semantic route | Intent model plus deterministic contract code | A recommendation constrained to a closed intent/action vocabulary. |
| Tool availability | Host | Derived from `ToolContract`; the model sees only canonical tools for the accepted intent. |
| Tool execution | Host | Requested tool names are checked again immediately before MCP execution. |
| Resource authorization | MCP server or downstream data system | Missing; dummy tools accept direct local calls without user identity or scopes. |

The following code-level ordering is the most important POC invariant:

```python
blocked_response = blocked_response_if_needed(question, trace)
if blocked_response is not None:
    return blocked_response

classification_or_response = await classify_request(question, settings, trace)
if isinstance(classification_or_response, AskResponse):
    return classification_or_response
```

It ensures a policy block prevents the intent model and catalog from being
contacted. The relevant implementation is
`src/harness_spike/agent_host/agent.py:28-47` and
`src/harness_spike/agent_host/agent.py:414-436`.

### 18.4 Deterministic Policy Gate and Surface Screen: Detailed Design Audit

The policy node is a local Python workflow step, not an MCP server and not an
agent. `policy_gate()` normalizes the prompt, runs 18 ordered checks, collects
their findings, and consolidates them with block-over-allow precedence. Its
output contract is only `allowed`, `reason`, and `matched_term`.

| Design decision | Current implementation | Industry rationale and audit judgment | Score |
| --- | --- | --- | ---: |
| Run before model and tools | `answer_question()` invokes `blocked_response_if_needed()` before intent, model, or catalog setup. Tests assert zero downstream calls for blocked prompts. | Strong defense-in-depth and cost-containment pattern. It creates a reproducible first boundary and a traceable short-circuit. | 5 |
| Separate deterministic and semantic decisions | Lexical/pattern rules remain in `policy/`; semantic classification remains in the intent node. | Aligns with Anthropic’s use of programmatic gates and specialized routing steps. It also makes false positives and false negatives attributable to a node. | 5 |
| Modular risk categories | Checks are named by prompt injection, jailbreak, secret access, unsupported workflow, scope expansion, destructive actions, exposure, PII, small-cell risk, and workflow allow rules. | Good maintainability and evaluation granularity. Categories should eventually map to owned enterprise policy IDs rather than free-text reasons. | 4 |
| Normalize adversarial text | NFKC normalization, case folding, invisible-character handling, punctuation compaction, leetspeak translation, boundary-aware obfuscation matching, and selected Damerau-Levenshtein checks are implemented. | Appropriate cheap preprocessing for known variants. It improves lexical coverage but cannot establish semantic safety. | 4 |
| Block findings override allow findings | `consolidate_findings()` returns the first block even when a workflow allow rule also fires. | Correct deny-overrides behavior for a high-risk POC. The check ordering still determines which reason is reported when several blocks fire. | 4 |
| Small positive workflow list | Metadata, safe aggregate, and narrowly described aggregate-SQL phrases can create an explicit allow finding. | A bounded positive route is preferable to relying only on a blacklist. However, these phrases do not represent user or resource authorization. | 3 |
| Distinguish explicit allow from no verdict | Explicit allow returns `reason=null`; no match returns `reason="no_deterministic_verdict"`. | The distinction is observable and useful. Encoding both states as `allowed=true` is semantically misleading and can be misused by a future caller that ignores `reason`. Use a three-state decision such as `block`, `route`, and `no_verdict`. | 2 |
| Permit patient-table metadata | `is_schema_metadata_request()` allows schema/column terms when row-output terms are absent; the scope and row-level modules use this exception. | Correctly fixes a demonstrated false positive and supports metadata discovery. The exception is phrase-based and must not become a substitute for column-level authorization. | 3 |
| Allow aggregate wording | Aggregate-only analytics remain allowed, but `check_row_level_request()` now checks row-level verbs and objects before applying the aggregate exemption. | Mixed prompts such as `count` plus `list records` are blocked deterministically, while schema metadata and aggregate-only requests remain available. | 4 |
| Prompt-injection and jailbreak blocklists | Known manipulation, persona, bypass, tool-escalation, and unbounded-retry phrases are blocked. | Useful high-precision signatures, but Anthropic explicitly treats prompt injection as unsolved and uses multiple classifier, permission, monitoring, and red-team layers. A blacklist cannot support a “jailbreak resistant” claim. | 2 |
| Surface-aware policy screening | A reusable deterministic screen now covers user input, tool metadata, tool results, and final answers with surface-specific checks. | This closes the POC’s obvious context blind spots, but it remains lexical/structural defense in depth; it is not authorization, a complete prompt-injection defense, or a security boundary for direct MCP callers. | 3 |
| Identity and purpose-aware authorization | No authenticated user, role, permitted purpose, resource scope, consent, or session risk enters the policy decision. | Missing by design for dummy data. HHS’s minimum-necessary guidance requires access to be limited by purpose, workforce role, data category, and conditions; lexical prompt classification cannot meet that requirement. | 1 |
| Server-side enforcement | The catalog MCP accepts direct localhost calls independently of the host’s policy decision. | Host scoping is valuable but is not a complete security boundary. A caller that reaches the MCP server directly bypasses the host. Production tools require their own authentication and authorization. | 1 |
| Trace privacy default | Raw prompts and model responses are hidden unless `LOG_RAW_PROMPTS` is enabled. | Correct privacy-preserving default for a POC. Production needs field-level redaction, retention, access controls, and audit of trace readers rather than only an on/off flag. | 4 |

Policy implementation references:

- Check registry and ordering: `src/harness_spike/policy/gates.py:21-47`.
- Deny-overrides consolidation and no-verdict state:
  `src/harness_spike/policy/consolidate.py:8-23` and
  `src/harness_spike/policy/result.py:27-45`.
- Normalization and metadata exception:
  `src/harness_spike/policy/normalize.py:28-120`.
- Aggregate shortcut and row-level detection:
  `src/harness_spike/policy/modules/pii.py:229-246`.
- Workflow allow terms:
  `src/harness_spike/policy/modules/workflow_authorization.py:7-59`.

### 18.5 Intent Classifier: Detailed Design Audit

The intent node is a single-turn model-backed router exposed as one MCP tool. It
is probabilistic, but it is not an autonomous agent: it cannot access the
catalog, execute a route, or answer the user. The configured Claude model must
produce one `emit_intent` tool call with this contract:

```json
{
  "intent": "schema_lookup",
  "confidence": 0.95,
  "risk_flags": [],
  "recommended_action": "get_table_schema",
  "needs_clarification": false
}
```

| Design decision | Current implementation | Industry rationale and audit judgment | Score |
| --- | --- | --- | ---: |
| Separate semantic routing call | Intent executes before catalog discovery and has no catalog tools. | Strong separation of concerns. Anthropic identifies routing as a useful workflow when inputs belong to distinct specialized categories. | 5 |
| Forced structured model output | The Messages API receives one JSON-schema tool and `tool_choice={"type":"tool","name":"emit_intent"}`; exactly one matching `ToolUseBlock` is required. | Directly follows Anthropic’s documented forced-tool pattern and avoids parsing free-form prose. | 5 |
| Runtime validation | Tool input is validated into `IntentResult`; the host validates the MCP result again. Intent and action values are closed `Literal` types with numeric confidence bounds. | Correct defense against malformed model or transport output. Add `extra="forbid"` to the Pydantic models because Pydantic otherwise ignores unexpected fields even though the API tool schema says `additionalProperties=false`. | 4 |
| Versioned prompt and explicit precedence | `INTENT_PROMPT_VERSION="v3"`; the prompt prioritizes policy probes, patient-level requests, unsafe SQL, safe SQL, metadata routes, general questions, then unknown. | Explicit precedence reduces mixed-intent ambiguity and supports regression comparison. The policy and taxonomy still require a named owner and change-control process. | 4 |
| Intent/action coherence enforcement | `EXPECTED_ACTION` and `enforce_intent_contract()` convert incoherent safe outputs to `unknown/clarify`; refusal intents are forced to `refuse`. | Strong deterministic wrapper around a probabilistic decision. The model recommends; code constrains. | 5 |
| Low-confidence fallback | Model-reported confidence below `0.70` becomes `unknown/clarify`. | Safe direction, but the value is an uncalibrated constant and model self-confidence is not a probability of correctness. It needs threshold calibration against held-out labels by risk class. | 2 |
| Ambiguity handling | `unknown`, low confidence, or `needs_clarification=true` stops before catalog access. | Aligns with Anthropic’s guidance that agents should pause rather than assume when user intent is unresolved. | 4 |
| Refusal enforcement | `policy_probe`, `patient_specific_request`, and `unsupported_sql_request` force a refusal; the host returns `allowed=false` without opening the catalog. | Correct defense-in-depth. The semantic safety classifier supplements but does not replace deterministic or server authorization controls. | 4 |
| Tool capability minimization | A single host-owned `ToolContract` registry derives intent-to-tool scopes, canonical model-facing schemas, result validators, and per-tool limits; live MCP inventory is checked against it. | Strong least-capability pattern and clearer ownership: intent recommends a route, while the host grants capability. | 4 |
| Execution-time scope check | The host checks every `ToolUseBlock.name` against `allowed_tools` again immediately before execution and records `tool.blocked` on mismatch. | Correctly treats model output as a proposal and the host as the execution authority. | 5 |
| MCP contract validation | The host fails closed when a required MCP tool is missing or its live input schema differs from the pinned contract; canonical host definitions are passed to the model. | Reduces trust in mutable tool metadata, but does not authenticate direct MCP callers or replace server-side authorization. | 3 |
| Execution budgets and stop states | A per-run budget now limits rounds, model/tool calls, per-tool calls, schema fan-out, bytes, wall time, MCP timeouts, and node `max_tokens`; `max_tokens` and refusal responses become explicit stop events. | Simple operational containment aligned with bounded tool-use practice. Provider-dollar accounting and independent server controls remain future work. | 3 |
| General-question isolation | `general_question` calls the main model with `tools=[]` and a system instruction forbidding claims of hospital-data or external-tool access. | Good capability isolation for a harmless route. Medical factual quality is outside this router’s evaluation and must not be represented as a clinical control. | 4 |
| Failure handling | Intent MCP exceptions, non-dictionary output, and validation errors produce `allowed=false`, `policy_reason="intent_classifier_uncertain"`, and no tool access. | Correct fail-safe behavior for operational classifier failures. | 5 |
| Valid-unknown response semantics | A valid `unknown/clarify` result stops before catalog, but its `AskResponse` omits `allowed=false`; the schema default therefore reports `allowed=true`. | Control flow is safe, but the API state is inconsistent with the operational-failure path and can mislead metrics or callers. Introduce an explicit decision/status enum or mark this response non-routable. | 2 |
| Unauthorized-tool response semantics | An out-of-scope tool request stops, but `unauthorized_tool_response()` also inherits `allowed=true`. | Execution is prevented, but observability and API semantics understate the block. This should be a typed policy enforcement outcome with `allowed=false`. | 2 |
| Safety-model independence | Intent and the main agent use the same configured Claude model family and API settings; the intent layer is prompt-based rather than a purpose-trained independent safety classifier. | This is adequate for routing research but creates correlated-failure risk. Fable’s published comparator uses separate safety classifiers as one layer among access controls, safety training, and offline monitoring. | 2 |
| Risk-flag contract | `risk_flags` is an unrestricted list of strings. | Flexible for experimentation but weak for metrics and enforcement. Replace with a versioned enum/taxonomy or treat flags as non-authoritative notes. | 2 |
| MCP transport security | Intent runs as unauthenticated HTTP on fixed localhost port 8002. | Appropriate for a local synthetic spike. It is not acceptable for a networked enterprise service without authenticated transport, authorization, service identity, and rate limits. | 1 |
| Evaluation corpus | `evals/intent.jsonl` contains 90 synthetic cases across baseline, injection, mixed-intent, obfuscation, false-positive, and tool-instruction categories; the runner supports repetitions and confusion metrics. | Strong evaluation structure. It matches Anthropic’s recommendation to combine code-based transcript checks with repeated, versioned regression tasks. | 4 |
| Current live evidence | The only stored full direct-intent report is prompt v1 with 270 observations: intent accuracy `0.7222`, action accuracy `0.7815`, 16 unsafe-to-safe routes, and 6 false positives. The implementation is now v3, but no stored full v3 live report exists. | Historical misses motivated the current design but cannot validate v3. A current repeated v3 run is required before presenting classifier quality or a release threshold. | 2 |

Intent and host implementation references:

- Output types and action contract:
  `src/harness_spike/mcp_servers/intent.py:13-68`.
- Host-owned tool contracts and route-derived scope:
  `src/harness_spike/agent_host/tool_registry.py`.
- Prompt precedence and examples:
  `src/harness_spike/mcp_servers/intent.py:85-139`.
- Deterministic contract normalization:
  `src/harness_spike/mcp_servers/intent.py:147-190`.
- Forced tool schema and API call:
  `src/harness_spike/mcp_servers/intent.py:193-268`.
- Host refusal, uncertainty, and failure paths:
  `src/harness_spike/agent_host/agent.py:86-189`.
- Host tool filtering and execution-time check:
  `src/harness_spike/agent_host/agent.py:54-76` and
  `src/harness_spike/agent_host/agent.py:451-490`.

### 18.6 Industry Comparator Matrix

These are concrete comparators, not claims that the POC has reproduced the
vendors’ complete safeguards.

| Published comparator | Relevant practice | Alignment in this POC | Material departure |
| --- | --- | --- | --- |
| [Anthropic: Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents) | Start with simple composable workflows; use routing for distinct categories; place programmatic gates between steps; keep tool interfaces clear. | Policy → intent → bounded loop is a small, understandable routed workflow. | The POC needs stronger ownership and release evidence before adding more nodes; more agent autonomy would not repair missing authorization. |
| [Anthropic Claude Platform: Define Tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools) | Tools use JSON Schema; `tool_choice` can force one named tool. | Intent forces one `emit_intent` call and validates its schema twice. | Pydantic extra fields are not explicitly forbidden at runtime, and model self-confidence remains uncalibrated. |
| [Claude Code Security](https://code.claude.com/docs/en/security) | Read-only defaults, explicit permission for sensitive actions, sandbox and working-directory boundaries, command-injection detection, and fail-closed handling for unmatched commands. | The host narrows tool availability, blocks out-of-scope tool names, has no-tool routes, and bounds rounds. | No sandbox, authenticated principal, interactive approval, filesystem/network boundary, or deny-by-default MCP authorization exists. The comparison supports the control pattern, not production equivalence. |
| [Anthropic: Fable 5 Safeguards](https://www.anthropic.com/news/fable-safeguards-jailbreak-framework) | Separate classifiers use risk categories and a tunable safety margin; classifiers are only one layer alongside access control, model safety training, offline monitoring, and red teaming. | The POC has a separate semantic classifier, risk precedence, deterministic blocks, tool-name scoping, and red-team corpora. | The classifier is a prompted general model, not a purpose-trained safeguard; there is no output classifier, identity/access-control layer, offline misuse monitoring, or measured safety-margin calibration. Fable is a useful defense-in-depth analogy, not proof that this hospital policy is sufficient. |
| [Anthropic: Constitutional Classifiers](https://www.anthropic.com/news/constitutional-classifiers) | Input and output classifiers are evaluated for jailbreak robustness and over-refusal tradeoffs. | The POC now screens user input, tool metadata, tool results, and final answers with deterministic surface-aware checks. | The POC has no purpose-trained classifier, calibrated safety margin, server-side authorization, or full output/data provenance control. |
| [Anthropic: Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | Combine code, model, and human graders; inspect transcripts and outcomes; repeat variable trials; turn mature capability cases into regression tests. | JSONL traces, deterministic policy/tool assertions, repeated intent trials, confusion metrics, and prompt versions follow this pattern. | Current v3 lacks a stored full live baseline; policy coverage is mostly test-corpus recall rather than a measured production distribution. |
| [MCP Introduction](https://modelcontextprotocol.io/docs/getting-started/intro) and [MCP Architecture](https://modelcontextprotocol.io/docs/learn/architecture) | MCP is a standard connection layer between AI applications and external tools/data. | FastMCP creates replaceable, discoverable, schema-defined service boundaries. | MCP is being used correctly as integration plumbing, but it must not be described as a security control by itself. |
| [MCP Authorization](https://modelcontextprotocol.io/docs/tutorials/security/authorization) and [MCP Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices) | Authorization is strongly recommended for user data, enterprise access, consent, per-user audit, and rate limiting; MCP security includes OAuth-related and confused-deputy risks. | None is needed to demonstrate localhost routing over dummy data. | Before real data, every MCP resource server needs authenticated service/user context, scoped grants, server-side checks, secure transport, and audit. |
| [HHS Minimum Necessary Guidance](https://www.hhs.gov/hipaa/for-professionals/privacy/guidance/minimum-necessary-requirement/index.html) and [HHS Security Rule Summary](https://www.hhs.gov/hipaa/for-professionals/security/laws-regulations/index.html) | Limit PHI by purpose, workforce role, data category, and conditions; implement authorization, authentication, audit, transmission security, and periodic evaluation. | Intent-based tool minimization is directionally consistent with minimum capability. | Prompt wording and intent do not establish identity, role, purpose, consent, or minimum-necessary data. The POC makes no HIPAA compliance claim. |

### 18.7 Defensible Demo Claims and Claims to Avoid

| Defensible for this POC | Not defensible from current evidence |
| --- | --- |
| “The harness separates a deterministic policy screen from probabilistic semantic routing.” | “The lexical policy gate guarantees prompt-injection or jailbreak resistance.” |
| “A blocked policy request stops before the intent model and catalog.” | “An `allowed=true` policy result authorizes data access.” |
| “The intent model emits a typed route; deterministic code validates and constrains it.” | “The model’s confidence of 0.95 means the route is 95% likely to be correct.” |
| “The host shows the main model only the tools mapped to the accepted intent and checks requested tool names again before execution.” | “MCP makes the tools secure or enforces enterprise permissions.” |
| “The system records enough observable events to grade route, tool, input, result, and final answer without hidden reasoning.” | “Passing unit tests proves current live-model safety or generalization.” |
| “All demonstrated catalog data and schemas are dummy fixtures.” | “This architecture is approved for PHI, Epic, BigQuery, or clinical use.” |
| “The design follows published routing, structured-tool, least-capability, defense-in-depth, and trace-evaluation patterns.” | “The POC implements the same safeguards as Claude Code or Fable 5.” |

Recommended concise demo explanation:

> Policy is a deterministic first-pass screen for known input boundaries and
> a surface-aware check around model context. Intent is a model-backed
> semantic router that can clarify or refuse but cannot authorize data. The
> host decides which tools are visible, screens tool metadata/results and
> final answers, and checks proposed calls before execution. MCP standardizes
> the connection to tools. In this POC all data is synthetic; production
> identity, authorization, output controls, and monitoring are intentionally
> absent.

### 18.8 Required Release Evidence and Production Preconditions

The following items are required to call the policy/intent POC demonstrated,
not production-ready:

1. Run the complete 90-case intent v3 corpus for at least three repetitions and
   retain the report with model ID, prompt version, confusion matrix,
   unsafe-to-safe routes, false positives, and confidence distribution.
2. Require zero unsafe-to-safe routes for `must_refuse` cases before using
   intent as an enforced secondary safety layer. Report usability errors
   separately rather than hiding them in an aggregate score.
3. Calibrate the confidence threshold on held-out human labels by risk class;
   until then, treat `0.70` as a conservative experiment, not a validated
   operating point.
4. Replace the overloaded `allowed` boolean with a typed decision such as
   `blocked`, `explicit_route`, `no_verdict`, `clarify`, and `refused`, or at
   minimum make valid-unknown and unauthorized-tool responses return a
   non-routable status consistently.
5. Add strict enums for risk flags, `extra="forbid"` model validation, policy
   IDs, policy/prompt ownership, and change approval.
6. Keep raw prompt/result logging disabled for ordinary runs and document the
   synthetic-only exception used for demonstrations.

Before any approved real metadata or patient-related data is introduced, the
following are production blockers:

1. Authenticate the user and service; carry role, purpose, tenant, consent,
   and resource scope into every decision.
2. Enforce authorization and minimum-necessary access inside each MCP server
   and downstream data system. Do not rely only on the host or intent label.
3. Add secure transport, credential isolation, per-tool scopes, rate limits,
   timeouts, network boundaries, and server-side audit.
4. Screen untrusted retrieved content and tool results before they enter the
   acting model, and validate/filter final output for identifiers, small cells,
   and unsupported disclosures.
5. Add purpose-trained or independently configured safety classification for
   high-risk input/output paths, with calibrated safety margins, monitored
   false positives, red-team testing, and drift alerts.
6. Add human approval for consequential or non-routine actions and keep data
   tools read-only until separately governed write workflows exist.
7. Define incident response, retention, trace-reader authorization, and
   periodic security/effectiveness review consistent with enterprise and HHS
   requirements.

### 18.9 Audit Verdict

| Area | Score | Verdict |
| --- | ---: | --- |
| High-level workflow decomposition | 4 | The small policy → intent → host design is easier to inspect and defend than a single unconstrained agent call. |
| Deterministic policy gate as a POC screen | 3 | Good traceable coverage of known risks, but blacklist/phrase logic and `allowed=true` no-verdict semantics limit its authority. |
| Intent structured-output and contract design | 4 | Forced typed output, action coherence, refusals, and host scoping are strong implementation decisions. |
| Intent calibration and current live evidence | 2 | The corpus is strong, but the current v3 prompt lacks a stored repeated full-suite baseline and the confidence cutoff is uncalibrated. |
| Tool least-capability enforcement | 4 | Tool filtering plus execution-time rechecking correctly keeps the model from granting itself capability. |
| Prompt-injection defense in depth | 3 | User-input, pinned tool-contract metadata, typed/bounded tool results, and final-answer screens now provide basic deterministic coverage alongside semantic refusal; independent classifiers, server authorization, provenance, and monitoring remain absent. |
| Production identity and authorization | 1 | Not implemented and intentionally out of scope for dummy data; mandatory before any governed data connection. |
| Observability and evaluation architecture | 4 | Versioned traces, deterministic assertions, repeated trials, and node-localized failures are well aligned with published agent-evaluation practice. |

Overall verdict: **defensible as a synthetic observability, routing, and
evaluation POC; not defensible as a production policy enforcement or healthcare
data-access system.** The strongest design choice is not the blacklist or the
classifier by itself. It is the layered allocation of responsibility: cheap
deterministic vetoes, typed semantic routing, host-owned least-capability tool
scope, observable traces, and regression evaluation. The primary departure
from mature industry practice is that identity, resource authorization,
input/output safety classification, and operational monitoring do not yet
surround those layers.
- Retrieval-augmented generation should connect model responses to an authoritative external knowledge base, provide source attribution, and keep external data current through refresh processes: AWS, [What is RAG?](https://aws.amazon.com/what-is/retrieval-augmented-generation/).
- Production RAG workflows should be evaluated as separate ingestion, embedding, retrieval, augmentation, and generation stages, with retrieval precision and generation faithfulness measured independently: Databricks, [End-to-End RAG Workflow](https://www.databricks.com/blog/rag-workflow).
- Agentic retrieval systems benefit from explicit tool surfaces, context management, permissioning, summarization, and MCP-compatible execution environments; these ideas inform the harness design, even though LangChain is not required for the MVP: LangChain, [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview) and [Deep Agents RAG](https://docs.langchain.com/oss/python/deepagents/rag).
