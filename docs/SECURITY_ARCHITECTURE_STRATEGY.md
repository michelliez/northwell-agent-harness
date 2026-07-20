# Security Architecture Strategy

> **Status:** Planning only. Nodes are built and tested one at a time.
> No implementation should begin without explicit approval per node.
> This document is noncanonical; current requirements live in `docs/PRD.md`.

---

## Repository Baseline

### What already exists

| Component | Location | Maturity |
|---|---|---|
| Deterministic policy screen | `policy/gates.py` + `policy/screen.py` + policy modules | POC first-pass defense; not a production security boundary |
| Three-layer obfuscation matching | `policy/normalize.py` | Solid: unicode → leetspeak → regex |
| Damerau-Levenshtein fuzzy matching | `policy/modules/pii.py` | Working, per-word only |
| Priority-ordered check chain | `policy/consolidate.py` | Works; see redesign note |
| LLM intent classifier (9 intents, 6 actions) | `mcp_servers/intent.py` | Working; confidence floor 0.70 |
| Bounded agent loop and execution budget | `agent_host/agent.py`, `agent_host/budget.py` | Working; rounds, calls, bytes, fan-out, and timeouts are bounded |
| Deterministic SQL validation (21 forbidden terms) | `mcp_servers/sql_validation.py` | Working for known-bad patterns |
| JSONL trace logger | `agent_host/trace_logger.py` | Working |
| Eval harness (115 cases across 3 suites) | `evals/` | Good foundation |

### What is partially implemented

- **`jailbreak.py`** exists but is not wired into `POLICY_CHECKS`. Dead code.
- **Intent `refuse` is not enforced** by the agent host. `agent.py` uses the intent for routing metadata only; it does not block when the classifier returns `refuse`.
- **SQL generation and validation servers** are scaffolded but use dummy data.
- **`run_safe_sql_workflow`** executes a 4-step pipeline but against the mock catalog.

### What is missing

- Egress filter — no response scanning for PHI/PII leakage
- Retrieval permission gate — described in the pipeline, absent in code
- SQL dry-run before execution
- Authentication and authorization at the API boundary
- Audit log tamper protection
- Red teaming feedback loop

### What should be redesigned

- **`consolidate_findings` returns `allowed()` by default** when no check fires. This is a blacklist: anything not explicitly blocked passes. For PHI, the default must be fail-closed.
- **Intent classifier `refuse` must block at the host**, not merely inform routing metadata.

### What is deferred

- Real BigQuery integration and network isolation
- PHI encryption at rest
- Multi-agent orchestration
- Federated identity and RBAC

---

## Design Principles

1. **Fail-safe by default.** When uncertain, block and request clarification. Never default to allow on uncertainty.
2. **Defense in depth.** No single layer is trusted to be sufficient. Each layer assumes upstream layers may fail.
3. **Determinism before reasoning.** Deterministic checks run before any LLM call.
4. **Least privilege at every boundary.** The agent receives only the tools and schema it needs for the current request.
5. **Separation of concerns.** Each layer has a defined interface and can be replaced independently.
6. **Explainability.** Every block produces a structured reason and matched term, logged immutably.
7. **Whitelist at the final gate.** A request is allowed only when it positively matches a defined safe workflow.
8. **No sensitive data in logs.** PHI, raw user prompts (unless explicitly opted in), and credentials are never written to trace files.
9. **Continuous adversarial improvement.** Every blocked prompt and red-team failure feeds the evaluation dataset and policy vocabulary.

---

## Threat Model

### Actors

| Actor | Goal | Sophistication |
|---|---|---|
| Curious internal user | Access data outside their role | Low |
| Determined internal actor | Extract PHI for profit or malice | Medium–High |
| External attacker (compromised account) | Exfiltrate bulk patient data | High |
| Prompt injection via document retrieval | Manipulate agent via malicious catalog entries | Medium |

### Attack Categories

| ID | Attack | Caught by |
|---|---|---|
| T1 | Direct PHI extraction (patient names, MRNs, DOBs) | L1 deterministic gate — pii.identifiers |
| T2 | Indirect re-identification (quasi-identifiers) | L1 — pii.small_cell_risk, pii.row_level_request |
| T3 | Prompt injection — ingress | L1 prompt_injection module + L2 intent classifier |
| T4 | Prompt injection — retrieval (malicious catalog docs) | L3 retrieval permission gate (missing) |
| T5 | Unsafe SQL (subquery, identifier column, destructive) | L6 sql_validation + dry-run (missing) |
| T6 | Jailbreak / hypothetical framing | L1 jailbreak module (unwired) + L2 intent classifier |
| T7 | Obfuscation bypass (leetspeak, spacing, typos) | L1 normalize.py three-layer matching |
| T8 | Output PHI leakage | L8 egress regex + L9 egress LLM (both missing) |
| T9 | Scope expansion (unauthorized tables/columns) | L1 operational_risk.scope_expansion + L3 (missing) |
| T10 | Credential / secret extraction | L1 operational_risk.secret_access |
| T11 | Audit evasion | L1 prompt_injection.tool_bypass |
| T12 | Agent spawning / privilege escalation | L1 tool_bypass + L2 policy_probe intent |
| T13 | Exfiltration (webhook, CSV export) | L1 operational_risk.local_execution_and_exfiltration |

---

## Layered Security Architecture

```
USER
  │
  ▼
┌──────────────────────────────────────────────────┐
│  LAYER 1 — DETERMINISTIC INGRESS GATE  [EXISTS]  │
│  policy_gate: all modules in priority order      │
│  Blocks in <5ms. No LLM call.                   │
└──────────────────────────────────────────────────┘
  │ allowed
  ▼
┌──────────────────────────────────────────────────┐
│  LAYER 2 — SEMANTIC INGRESS GUARD  [PARTIAL]     │
│  LLM intent classifier, closed tool schema       │
│  Confidence floor 0.70. Fails closed.            │
│  Refusal and uncertainty stop before catalog.    │
└──────────────────────────────────────────────────┘
  │ intent + recommended_action
  ▼
┌──────────────────────────────────────────────────┐
│  LAYER 3 — RETRIEVAL PERMISSION GATE  [MISSING]  │
│  Deterministic: maps (intent, role) → permitted  │
│  tables × columns. Narrows tool exposure.        │
└──────────────────────────────────────────────────┘
  │ authorized schema fragment
  ▼
┌──────────────────────────────────────────────────┐
│  LAYER 4 — AGENT LLM  [EXISTS]                  │
│  Bounded tool rounds. No direct DB access.       │
│  Tools exposed = only those authorized by L3.   │
└──────────────────────────────────────────────────┘
  │ tool calls
  ▼
┌──────────────────────────────────────────────────┐
│  LAYER 5 — TOOL EXECUTION GATE  [MISSING]        │
│  Per-call: validates tool name is authorized,    │
│  validates parameters, enforces read-only.       │
└──────────────────────────────────────────────────┘
  │ tool results
  ▼
┌──────────────────────────────────────────────────┐
│  LAYER 6 — SQL VALIDATION + DRY-RUN  [PARTIAL]  │
│  Deterministic lexical check EXISTS.             │
│  MISSING: dry-run EXPLAIN before execution.      │
└──────────────────────────────────────────────────┘
  │ validated + dry-run passed
  ▼
┌──────────────────────────────────────────────────┐
│  LAYER 7 — AGENT RESPONSE (generated text)       │
└──────────────────────────────────────────────────┘
  │ candidate response
  ▼
┌──────────────────────────────────────────────────┐
│  LAYER 8 — DETERMINISTIC EGRESS GATE  [MISSING] │
│  Regex PHI scanner: MRN, DOB, SSN, phone,        │
│  email, known-name patterns.                     │
└──────────────────────────────────────────────────┘
  │ passed
  ▼
┌──────────────────────────────────────────────────┐
│  LAYER 9 — SEMANTIC EGRESS GUARD  [MISSING]      │
│  LLM PHI/PII scanner on final response.          │
│  Checks leakage, hallucinated permissions,       │
│  unsafe SQL explanations.                        │
└──────────────────────────────────────────────────┘
  │ cleared
  ▼
USER
```

Async audit logging to an append-only store fires at every layer transition.

---

## Node Build Order

Each node is built, tested, and signed off before the next begins.

| # | Node | Layer | Status | Priority |
|---|---|---|---|---|
| 1 | Wire `jailbreak.py` into `POLICY_CHECKS` | L1 | Not started | High |
| 2 | Enforce intent `refuse` in `agent.py` | L2 | Not started | Critical |
| 3 | Retrieval permission gate | L3 | Not started | High |
| 4 | Tool execution gate in `mcp_bridge.py` | L5 | Not started | High |
| 5 | SQL dry-run (EXPLAIN before execution) | L6 | Not started | High |
| 6 | Deterministic egress PHI scanner | L8 | Not started | Critical |
| 7 | Semantic egress guard (LLM) | L9 | Not started | Medium |
| 8 | Audit log tamper protection | Cross-cutting | Not started | Medium |
| 9 | Continuous red-team feedback loop | Cross-cutting | Not started | Medium |

---

## Layer Responsibilities (detail)

### Layer 1 — Deterministic Ingress Gate *(exists)*

- Runs before any LLM call. Under 5ms.
- Deterministic only: regex, normalized phrase matching, Damerau-Levenshtein fuzzy matching.
- **Redesign needed:** `consolidate_findings` returns `allowed()` as its final default (blacklist). If neither a block nor a positive-whitelist match fires, the result must be treated as unknown and escalated to L2, not silently allowed.
- **Fix needed:** Wire `jailbreak.py` into `POLICY_CHECKS` between `prompt_injection.policy_manipulation` and `operational_risk.secret_access`.

### Layer 2 — Semantic Ingress Guard *(partial)*

- Runs after L1 passes. One LLM call (~200–400ms).
- Uses closed tool schema (`emit_intent`, `additionalProperties: False`).
- Confidence floor 0.70 — fails closed on low confidence.
- `recommended_action == "refuse"` (intents: `policy_probe`, `patient_specific_request`, `unsupported_sql_request`) blocks before catalog access.
- Unknown / low-confidence → clarification request, never allow execution.

### Layer 3 — Retrieval Permission Gate *(missing)*

- Runs after intent classification. Before the agent loop.
- Deterministic: maps `(intent, user_role)` → `permitted_tables × permitted_columns`.
- Narrows the schema exposed to the agent. Agent cannot decide its own permissions.
- Implementation path: static mapping dict to start; graduate to policy-as-code later.

### Layer 4 — Agent LLM *(exists)*

- Only tools authorized by L3 are exposed via MCP.
- `MAX_TOOL_ROUNDS` plus total/per-tool calls, byte limits, schema fan-out,
  timeouts, and explicit model stop states are enforced.
- No direct database access — all data access goes through L5.
- System prompt must not contain secrets, credentials, or production schema.
- Provider safety (Claude Constitutional AI) acts as a residual backstop only — not a primary control.

### Layer 5 — Tool Execution Gate *(host implementation; server authorization pending)*

- Runs on every tool call before it reaches the MCP server.
- Validates: (a) tool name is in the authorized set from L3; (b) parameters contain no injection payloads.
- The host-owned `ToolContract` registry checks tool names, canonical schemas,
  arguments, results, and execution limits before forwarding.
- MCP servers require the configured local service token in this POC; server-side
  identity, principal/resource authorization, and rate limits are still required
  before real data.

### Layer 6 — SQL Validation + Dry-Run *(partial)*

- Deterministic lexical validation exists: 21 forbidden terms, non-SELECT check, identifier column detection, SELECT *, multiple statements.
- **Missing:** dry-run EXPLAIN before execution. Catches structural issues that lexical validation misses (e.g., correlated subquery returning one row per patient).

### Layer 7 — Agent Response

- The agent's generated text. Input to L8 and L9. No special logic.

### Layer 8 — Deterministic Egress Gate *(missing)*

- Runs before response reaches the user. Under 10ms.
- Regex-based PHI pattern detection: MRN numeric ranges, SSN, DOB date patterns, phone, email.
- On match: block response, log matched pattern name (not the matched value), return safe error.
- Never returns matched text to the user.

### Layer 9 — Semantic Egress Guard *(missing)*

- Runs after L8 passes. One LLM call on the final response.
- Lightweight model preferred (task-specific PHI detector).
- Checks: PHI not caught by regex, hallucinated permissions, unsafe SQL explanation, business scope violation.
- Confidence floor: uncertain results block, not allow.
- Authorization context (permitted data categories, business scope) feeds the evaluation prompt.

---

## Guardrail Taxonomy

### Always deterministic

| Check | Reason |
|---|---|
| PHI identifier matching (MRN, DOB, SSN, phone) | Zero-tolerance; regex is precise and fast |
| Destructive SQL keyword detection | Well-defined; unambiguous |
| Policy manipulation phrases | Exact/fuzzy phrase matching; speed matters |
| SQL forbidden term validation | Must be provably correct |
| SQL dry-run EXPLAIN | Database engine is authoritative |
| Tool name authorization | Must not be arguable; binary list membership |
| Audit logging | Must be deterministic and tamper-evident |

### Always LLM

| Check | Reason |
|---|---|
| Intent classification | Semantic understanding of natural language |
| Jailbreak / hypothetical framing | Requires contextual reasoning across full prompt |
| Egress coherence (hallucinated permissions, unsafe SQL explanation) | Requires understanding of generated prose |
| Novel obfuscation not yet in blocklist | Requires semantic generalization |

### Hybrid (deterministic first, LLM second)

| Check | Reason |
|---|---|
| PHI in response (egress) | Regex catches structured patterns; LLM catches prose-embedded PHI |
| Prompt injection | Phrase matching catches known patterns; LLM catches novel framing |
| Business scope validation | Keyword allowlist for simple cases; LLM for ambiguous phrasing |

---

## Logging and Auditing Strategy

### What to log (immutably, append-only)

```
run_id                    — UUID per request
ts                        — Unix timestamp, millisecond precision
event                     — layer-specific event name
layer                     — L1 through L9
decision                  — allow | block | clarify
reason                    — human-readable string
matched_term              — term that triggered (not the matched value)
intent                    — from L2, propagated through subsequent events
recommended_action        — from L2
confidence                — from L2 and L9
tools_authorized          — list of tool names from L3 (not parameters)
tools_called              — list of tool names called in L4 (not parameters)
sql_structure             — SQL with values redacted
response_length           — character count (not the response itself)
policy_modules_triggered  — list of module names that produced a verdict
```

### What never enters the log

- Raw user prompt, SQL query results, tool call parameters, and final response
  text in the default `TRACE_CONTENT_MODE=metadata`
- Metadata-only HMAC digests and byte/item counts may be retained for correlation
- API keys, environment variables, credentials
- Any field that may contain PHI

### Production requirement

Write-once object storage (S3 with object lock or equivalent), signed log entries, retention policy aligned with HIPAA (minimum 6 years for PHI-adjacent access logs).

---

## Whitelist vs Blacklist

The repository today is a **blacklist** architecture: `consolidate_findings` returns `allowed()` as its final default. For a healthcare application handling PHI this is the wrong default.

The proposed architecture uses **both**:

- **Blacklist (L1):** Fast, cheap, explicit rejection of known-bad patterns.
- **Whitelist (L3):** A request proceeds to the agent loop only if it matches a defined safe workflow. Anything outside the whitelist — even if it passed the blacklist — is clarified or blocked.

This eliminates the current architecture's most dangerous property.

---

## Task-Specific vs General Models

| Layer | Recommendation |
|---|---|
| L1 policy screen | Keep deterministic. No LLM. Apply at user-input and model-context boundaries. |
| L2 intent classification | LLM now; consider fine-tuned classifier when taxonomy stabilizes |
| L6 SQL validation | Keep deterministic. LLM introduces non-determinism at a safety-critical layer. |
| L8 egress regex | Keep deterministic. |
| L9 egress PHI detection | Strongest case for a task-specific fine-tuned model. Faster and more accurate than prompting a general-purpose LLM. |

---

## Failure Handling

| Layer | Failure type | Behavior |
|---|---|---|
| L1 deterministic | Module throws exception | Fail closed: treat as block, log exception |
| L2 intent LLM | API timeout / error | Fail closed: return clarification response |
| L2 intent LLM | Missing `emit_intent` tool call | Fail closed (existing RuntimeError behavior) |
| L2 intent LLM | Confidence < 0.70 | Fail closed: return clarification response |
| L3 retrieval gate | Unknown intent | Return empty authorized tool set; no tools exposed |
| L5 tool gate | Unauthorized tool name | Terminate agent loop, log violation |
| L6 SQL validation | Parse error | Treat as invalid; request regeneration or terminate |
| L6 SQL dry-run | Timeout | Treat as invalid; do not execute |
| L8 regex scan | Scanner error | Fail closed: block response |
| L9 egress LLM | API timeout / error | Fail closed: block response, return safe error to user |
| Audit logger | Write failure | Never fail the request; log to stderr; alert |

A failed check always returns a generic safe refusal to the user. The user is never told which layer failed or why.

---

## Continuous Red Teaming

```
Red-team attack attempt
    │
    ▼
Policy gate / intent classifier runs
    │
    ├── BLOCKED correctly → add to confirmed-blocked eval suite
    │
    └── PASSED (false negative) →
            ├── Analyze: which layer failed?
            ├── Deterministic gap → add term to appropriate module
            ├── LLM classification gap → add to intent eval JSONL
            ├── Novel obfuscation → add to normalize.py / fuzzy matching
            └── Rebuild → re-run full eval suite → confirm fix
```

### Missing from the current loop

- Automated red-team prompt generation
- CI gate: eval suite runs on every pull request; failing eval blocks merge
- Metrics dashboard: false positive rate, false negative rate, per-module block rate over time
- Differential evaluation: compare policy gate behavior before and after a module change

---

## Open Research Questions

1. **Compound phrase fuzzy matching.** The Damerau-Levenshtein implementation applies per-word only. An attacker who corrupts both words of a multi-word blocked term simultaneously may evade detection.
2. **Small-cell re-identification accumulation.** A sequence of individually-safe aggregate queries can together re-identify an individual. Session-level query history tracking is not implemented.
3. **Retrieval injection.** If catalog documentation is editable by an adversary, embedded prompt injection payloads reach the agent through `search_docs`. Retrieved documents must be sanitized before injection into context.
4. **Egress model calibration.** A PHI detector will have false positives on legitimate clinical terminology. Threshold tuning methodology is unresolved.
5. **Multi-turn conversation state.** The architecture is stateless per request. Intent from earlier turns may influence the safety classification of later turns.
6. **Provider model version drift.** When the underlying LLM version changes, intent classification behavior may shift. The eval harness must detect this before production deployment.

---

## Reference Diagram Notes

### sec-infra.png

Depicts: User → Filter-IN → Agentic Llama → Filter-OUT → User, with an independent Guard Llama evaluating both directions. Optional RAG feeds the Guard.

**Retained:** Independent guard evaluation concept. Two-filter structural separation.
**Modified:** Split Guard into Ingress Guard (L2) and Egress Guard (L9). Deterministic layers precede LLM guards.
**Rejected:** Guard LLM as sole security mechanism. Tool-call mediation is absent in the diagram — this gap is addressed by L5.

### sec-data.png

Depicts: User → Guard LLM → Agentic LLM → Guard LLM → User, with a PII component feeding the Agentic LLM.

**Retained:** Guard wraps both request and response.
**Modified:** PII is egress-only (L8); it must never feed the agent as a data source.
**Rejected:** Single LLM as sole security. No deterministic layer is shown — this is insufficient for PHI compliance.
