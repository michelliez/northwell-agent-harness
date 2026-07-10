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
