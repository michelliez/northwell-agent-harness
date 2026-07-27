# ADR 001: In-Process Graph Trunk and MCP as an External Boundary

- Status: Accepted
- Date: 2026-07-27
- Supersedes: the MCP-host trunk merged as `origin/main` PR #7 (`f8439d7`)
- Related: [`docs/PRD.md`](../PRD.md), [`docs/RAG.md`](../RAG.md)

## Context

At the time of this decision the repository had four live branches, each about
28 commits ahead of a shared base from 2026-07-20, and nothing had merged back
to trunk in a week. Two contributors had independently written a
`retrieval/client.py`. `genq` merged to `main` during this review, which made
the MCP-service architecture the trunk while a parallel branch was replacing it.

Two architectures existed:

| | MCP host (`main`, `rag`, `genq`) | Graph pipeline (`simplified-pipeline`) |
| --- | --- | --- |
| Lifecycle | `agent.py` dispatch to `workflows/*` | one `StateGraph` over typed `AgentState` |
| Services | 5 FastMCP servers over localhost HTTP | direct typed function calls |
| Process model | `MCPServerManager` subprocess auto-start | single process |
| Tool authority | `tool_registry.TOOL_CONTRACTS` + execution-time recheck | fixed graph edges |
| Clarification | terminal `AskResponse` | `interrupt()` / `Command(resume=)` on a checkpointer |
| `src/` size | 10,678 LOC (tests 6,170) | 6,618 LOC (tests 3,904) |

Both satisfied the deterministic-policy-first invariant. The difference was cost
of ownership, not safety posture.

Two design principles govern this decision:

1. The architecture must be defensible against published practice for agentic
   systems, primarily Anthropic's
   [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)
   and [Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents),
   and the [MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture)
   and [security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices).
2. It must be the simplest structure that is robust, observable, and extensible.

## Decision

### 1. The product is a data analytics agentic system

`simplified-pipeline`'s PRD framing is adopted: the deliverable is a governed
analytics agent over approved schema documentation. Evaluation is a required
property of that system and its release gate — not a separate product.

The earlier "evaluation harness as the product" framing is retired. It produced
three parallel half-built evaluation paths (`evals/runner.py`,
`evals/retrieval_evaluator.py`, `evals/intent_assertions.py`) and three MVP
contracts (`ScenarioSpec`, `RunTrace`, `EvalResult`) that were specified in the
PRD but never existed as code on any branch.

### 2. One in-process LangGraph owns the request lifecycle

`simplified-pipeline`'s graph becomes trunk:

```text
input policy -> intent
  |-> refusal
  |-> clarification (interrupt/resume)
  |-> general answer
  `-> retrieval permission -> retrieval -> context gate
        |-> documentation answer
        `-> query plan -> plan safety -> SQL generation -> SQL validation
              `-> bounded repair cycle -> execution not configured
-> result safety -> citations -> final answer -> bounded follow-up
```

The layered allocation of responsibility is preserved unchanged, because it is
the strongest property of the system and the one the PRD §18 audit scored
highest: deterministic screen, then typed semantic route, then host-granted
capability, then screened output.

### 3. LangGraph is used for control flow only, never for prompt construction

*Building Effective Agents* explicitly cautions that agent frameworks add
abstraction layers that obscure the underlying prompts and responses and make
debugging harder, and recommends using model APIs directly first. That caution
is accepted and answered by constraint rather than by avoiding the framework:

**Permitted uses of LangGraph:** `StateGraph`, nodes, fixed and conditional
edges, state reducers, `interrupt()` / `Command(resume=)`, checkpointers.

**Forbidden uses:** prompt templates, output parsers, chains, agent executors,
memory abstractions, retriever or vector-store wrappers, or any construct that
sits between this codebase and `client.messages.create`.

Every model call is a direct Anthropic SDK call with a prompt constant declared
in this repository. The forced-tool intent call keeps
`tool_choice={"type": "tool", "name": "emit_intent"}` and its own JSON schema.

This is the defensible position and should be stated as such: *LangGraph
supplies durability and control flow; prompts and responses stay fully visible.*

### 4. MCP is an external integration boundary, not internal plumbing

Internal MCP services are removed. Intent classification, SQL generation, SQL
validation, and retrieval become direct typed function calls.

MCP's value is crossing a trust or ownership boundary. Exposing this
repository's own pure functions over localhost HTTP bought five processes, port
pinning, subprocess lifecycle management, a shared bearer token, serialization,
timeouts, and `validate_live_inventory()` — which exists only to defend against
schema drift created by leaving the process. It bought no isolation, no
identity, and no authorization. MCP's own documentation is explicit that the
protocol standardizes integration and does not by itself provide authorization
or sandboxing.

Removed: `mcp_servers/` (1,370 LOC), `mcp_bridge.py` (71),
`mcp_process_manager.py` (162), `retrieval/mcp_server.py` (737) — about 2,340
LOC of transport and process lifecycle, plus roughly 600 LOC of boundary
re-validation in `tool_registry.py` and `tool_execution.py`. The genuinely
reusable domain logic inside those files — the intent prompt and contract,
SQLGlot validation, SQL generation, retrieval search — survives as plain
modules.

**MCP is retained in two forms:**

- As a **client**, for connecting to real external services (Epic, BigQuery)
  when those integrations exist. That is the boundary MCP is designed for.
- Optionally as a **server** exposing retrieval to other teams as a product
  surface. If that is built, the host still calls retrieval in-process; the
  server is an additional consumer, never a link in our own request path.

Reintroducing an internal MCP transport requires a new ADR and a concrete
external integration requirement.

### 5. Host-owned tool scope shrinks but does not disappear

`simplified-pipeline` deleted `tool_registry.py` entirely and enforces scope
through fixed graph edges. For a fully fixed pipeline that is stronger and
simpler, and it is kept.

It stops being sufficient the moment a node lets the model choose a tool. The
bounded exploration loop — the model iterating over `find_table_doc`,
`get_doc_section`, and `search_columns` — is the one genuinely agentic node in
the system, in the *Building Effective Agents* sense of an agent directing its
own process, and it is the most valuable thing to evaluate.

It is therefore reinstated as a single node backed by a much smaller registry
(`agent_host/tools.py`): a map of intent to permitted tool names, Pydantic
input and output models per tool, and the execution-time name recheck. Model
output remains a proposal; the host remains the execution authority.

### 6. The trace contract becomes typed

`RunTrace` events become a Pydantic discriminated union. The duplicate
observability path is removed: `simplified-pipeline` accumulated events both in
JSONL through `TraceLogger` and in a `trace_events` state channel. JSONL is the
single source of truth.

This is what makes graders, the trace viewer, and framework-neutral adapters
type-safe at once, and it is what allows LangGraph to be replaced later without
rewriting evaluation. `TRACE_CONTENT_MODE=metadata` redaction-by-default is
retained.

### 7. Chunking is pinned to `section-table-v3`

The chunker on `simplified-pipeline` and `origin/genq` was verified identical
apart from line wrapping and the default database path: `CHUNKER_VERSION =
"section-table-v3"`, `CHUNK_TARGET_CHARS = 3200`, `CHUNK_HARD_MAX_CHARS = 4800`,
row-batched `table_to_chunks`. No port was required.

`CHUNKER_VERSION` must be incremented on any change to chunk boundaries, and
retrieval benchmark results are only comparable within one chunker version.

## Safety corrections required by this decision

Two regressions were introduced by the port to LangGraph and must be fixed as
part of the cutover.

**Intent typing regression.** `nodes/intent_nodes._RawIntentDecision` declares
`intent: str` with no `extra="forbid"`, where the MCP implementation used a
closed `IntentName` literal, a closed `RecommendedAction` literal, and
`extra="forbid"`. The `EXPECTED_ACTION.get(intent, "clarify")` fallback keeps
behaviour fail-closed, so this is not an open hole, but the closed vocabulary is
restored.

**Column safety trust inversion.** `retrieval/client._infer_safety()`
keyword-matches over *retrieved documentation text* to label columns
`identifier`, `sensitive`, or `safe_aggregate`, and that label feeds the SQL
safety gate. Untrusted retrieved content must never widen a hard boundary. The
function becomes deny-by-default: evidence may only narrow permission, `unknown`
blocks, and any column reaching SQL generation must be positively established as
safe.

## Consequences

**Gained.** One process and one `uv run` command. About 38% less source. The
graph is a declared object, so routes are inspectable and testable without
running a model. Clarification becomes durable resume instead of a dead-end
response. The framework caution is answered explicitly rather than ignored.

**Lost.** The multi-tool exploration loop, until item 5 reinstates it. Any
ability for another process to call these services over MCP, until an external
consumer justifies it.

**Costs.** LangGraph and `langchain-core` become dependencies, and the forbidden
uses in item 3 need review discipline. `InMemorySaver` is process-local, so
threads do not survive restart; a durable checkpointer is required before
multi-turn state is a product claim. `rag-embeddings` must rebase onto the new
trunk before landing.

**Unchanged.** No production authorization, identity, or PHI handling exists.
This ADR does not alter the PRD's production preconditions, and passing tests
still establishes no compliance claim.

## Fallback

The MCP-host architecture is preserved as a working, restorable state.

| Ref | Points at | Meaning |
| --- | --- | --- |
| `fallback/mcp-host` | `f8439d7` | Frozen branch. Last reviewed MCP trunk (PR #7). No upstream, takes no new commits. |
| `archive/mcp-host-main-20260727` | `f8439d7` | Immutable tag for the same commit. |
| `archive/mcp-host-genq-20260727` | `f642a5e` | `genq` head at cutover, including GenQ and the FAISS baseline. |
| `archive/mcp-host-rag-20260727` | `171da81` | `rag` head at cutover. |

Every other branch head at cutover is tagged `archive/<name>-20260727`.

The tag is the durable artifact; the branch exists for discoverability in
`git branch`. Both point at the same commit, so a deleted branch loses nothing.
`fallback/mcp-host` deliberately has no upstream and must not accumulate
commits — a fallback that drifts is not a fallback. Fixes belong on trunk.

Restore the MCP host for inspection:

```bash
git switch --detach archive/mcp-host-main-20260727
uv sync
uv run --no-editable agent-harness "<question>"
```

Revive it as a working line, which requires a new ADR superseding this one:

```bash
git switch -c mcp-host-revival archive/mcp-host-main-20260727
```

Retire these refs only when an external MCP integration has shipped and the
graph trunk has held for a full release cycle.

## Alternatives considered

**Keep the MCP host and delete auto-start only.** Removes the coupling that
prompted this review but keeps five processes, the HTTP hop, and the schema
re-validation that exists solely because of it. Treats the symptom.

**Keep MCP internally as a rehearsal for production service boundaries.** The
rehearsal is misleading: it exercises serialization and discovery while omitting
identity, scoped authorization, and audit — the parts that are actually hard and
that MCP's security guidance requires. It teaches the easy half.

**Write the state machine by hand instead of adopting LangGraph.** Closest to
the framework caution, and viable — the graph is roughly 390 lines. Rejected
because durable checkpointing and interrupt/resume are the parts that are
tedious and easy to get wrong, and item 3's constraints already contain the
abstraction to control flow. Reversible: the typed trace contract in item 6 is
what keeps it reversible.

**Keep both architectures behind an adapter.** Two lifecycles, two tool
authorities, two trace shapes, twice the tests, for a POC with one deployment
target. The archive tags provide the real option value at zero carrying cost.
