"""The observable trace contract.

This module is the single source of truth for what a run can emit. Nodes write
events, `trace_viewer` renders them, and `evals` grades them -- all three read
their vocabulary from here rather than repeating string literals.

That indirection is the point. The three consumers had drifted: the viewer still
classified MCP-era names like `intent.classification.request` that no node
emits, so almost every event in a real run rendered as "unknown". A closed
vocabulary plus a registry makes that class of drift a test failure.

`TraceDomain` is the coarse grouping carried on every JSONL line. It was
previously derived by matching the event-name prefix, which mislabels
`answer.ready` as policy because it starts with "answer". Domain now comes from
this registry, so each event says what it is instead of being guessed at.

Adding an event means adding one `TraceEventName` member and one `EVENT_SPEC`
row. `test_trace_contract.py` fails until both exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

TraceDomain = Literal[
    "policy",
    "intent",
    "retrieval",
    "sql",
    "lifecycle",
    "operational",
]

# Whether the event records a normal step, a deliberate stop, or a failure.
EventStatus = Literal["ok", "blocked", "error"]

TraceEventName = Literal[
    "policy_gate.checked",
    "policy_gate.allowed",
    "request.blocked",
    "content.screened",
    "answer.blocked",
    "result_safety.passed",
    "intent.classified",
    "intent.empty_question",
    "intent.model_error",
    "intent.clarification_requested",
    "intent.clarification_exhausted",
    "retrieval_permission.checked",
    "retrieval.started",
    "retrieval.completed",
    "retrieval.error",
    "retrieval.content_blocked",
    "context_gate.context_available",
    "context_gate.schema_snapshot_built",
    "context_gate.schema_build_error",
    "context_gate.no_context_clarification",
    "context_gate.no_context_exhausted",
    "exploration.completed",
    "exploration.stopped",
    "exploration.error",
    "exploration.tool_completed",
    "exploration.tool_content_blocked",
    "exploration.fallback_retrieval",
    "exploration.fallback_error",
    "exploration.fallback_content_blocked",
    "query_plan.built",
    "query_plan.no_schema_snapshot",
    "plan_safety.approved",
    "plan_safety.no_plan",
    "plan_safety.unknown_safety_columns",
    "generate_sql.completed",
    "generate_sql.no_plan",
    "generate_sql.error",
    "validate_sql.completed",
    "validate_sql.no_sql",
    "validate_sql.routing_to_repair",
    "validate_sql.failed_final",
    "execution_not_configured",
    "output_safety.started",
    "output_safety.completed",
    "output_safety.skipped",
    "output_safety.error",
    "output_safety.budget_exceeded",
    "output_safety.invalid_response",
    "output_safety.validation_error",
    "general_answer.started",
    "general_answer.completed",
    "general_answer.budget_exceeded",
    "general_answer.error",
    "documentation_answer.started",
    "documentation_answer.completed",
    "documentation_answer.budget_exceeded",
    "documentation_answer.error",
    "answer.ready",
]


class UnknownTraceEvent(KeyError):
    """Raised when code tries to record an event outside the contract.

    Event names are literals in source, never derived from user input, so this
    is always a programming error and should fail loudly rather than write an
    event no consumer knows how to read.
    """


@dataclass(frozen=True)
class EventSpec:
    """What one event means to the viewer and to graders."""

    domain: TraceDomain
    status: EventStatus
    label: str
    # True when the event proves the request got past the deterministic input
    # gate. Graders assert that none of these appear in a blocked run.
    downstream: bool = False


def _s(domain: TraceDomain, status: EventStatus, label: str, downstream: bool = False) -> EventSpec:
    return EventSpec(domain, status, label, downstream)


EVENT_SPEC: dict[TraceEventName, EventSpec] = {
    "policy_gate.checked": _s("policy", "ok", "Input screened"),
    "policy_gate.allowed": _s("policy", "ok", "Input allowed"),
    "request.blocked": _s("policy", "blocked", "Request blocked"),
    "content.screened": _s("policy", "ok", "Content screened"),
    "answer.blocked": _s("policy", "blocked", "Answer blocked"),
    "result_safety.passed": _s("policy", "ok", "Answer screen passed"),
    "intent.classified": _s("intent", "ok", "Intent classified", True),
    "intent.empty_question": _s("intent", "blocked", "Empty question"),
    "intent.model_error": _s("intent", "error", "Intent classifier failed"),
    "intent.clarification_requested": _s("intent", "ok", "Clarification requested"),
    "intent.clarification_exhausted": _s("intent", "blocked", "Clarification exhausted"),
    "retrieval_permission.checked": _s("retrieval", "ok", "Retrieval permitted"),
    "retrieval.started": _s("retrieval", "ok", "Retrieval started", True),
    "retrieval.completed": _s("retrieval", "ok", "Retrieval completed", True),
    "retrieval.error": _s("retrieval", "error", "Retrieval failed"),
    "retrieval.content_blocked": _s("retrieval", "blocked", "Retrieved content blocked"),
    "context_gate.context_available": _s("retrieval", "ok", "Context available"),
    "context_gate.schema_snapshot_built": _s("retrieval", "ok", "Schema evidence built"),
    "context_gate.schema_build_error": _s("retrieval", "error", "Schema evidence failed"),
    "context_gate.no_context_clarification": _s("retrieval", "ok", "No context, clarifying"),
    "context_gate.no_context_exhausted": _s("retrieval", "blocked", "No context found"),
    "exploration.completed": _s("retrieval", "ok", "Exploration completed", True),
    "exploration.stopped": _s("retrieval", "blocked", "Exploration stopped"),
    "exploration.error": _s("retrieval", "error", "Exploration failed"),
    "exploration.tool_completed": _s("retrieval", "ok", "Exploration tool ran", True),
    "exploration.tool_content_blocked": _s("retrieval", "blocked", "Tool result blocked"),
    "exploration.fallback_retrieval": _s("retrieval", "ok", "Fell back to retrieval"),
    "exploration.fallback_error": _s("retrieval", "error", "Fallback failed"),
    "exploration.fallback_content_blocked": _s("retrieval", "blocked", "Fallback blocked"),
    "query_plan.built": _s("sql", "ok", "Query plan built", True),
    "query_plan.no_schema_snapshot": _s("sql", "blocked", "No schema evidence"),
    "plan_safety.approved": _s("sql", "ok", "Plan approved"),
    "plan_safety.no_plan": _s("sql", "blocked", "No plan to check"),
    "plan_safety.unknown_safety_columns": _s("sql", "blocked", "Unknown column safety"),
    "generate_sql.completed": _s("sql", "ok", "SQL generated", True),
    "generate_sql.no_plan": _s("sql", "blocked", "No plan for generation"),
    "generate_sql.error": _s("sql", "error", "SQL generation failed"),
    "validate_sql.completed": _s("sql", "ok", "SQL validated", True),
    "validate_sql.no_sql": _s("sql", "blocked", "No SQL to validate"),
    "validate_sql.routing_to_repair": _s("sql", "ok", "Routing to repair"),
    "validate_sql.failed_final": _s("sql", "blocked", "SQL rejected"),
    "execution_not_configured": _s("sql", "blocked", "Execution not configured"),
    "output_safety.started": _s("operational", "ok", "Output assessment started"),
    "output_safety.completed": _s("operational", "ok", "Output assessed"),
    "output_safety.skipped": _s("operational", "ok", "No answer to assess"),
    "output_safety.error": _s("operational", "error", "Output assessment failed"),
    "output_safety.budget_exceeded": _s("operational", "error", "Assessment budget exceeded"),
    "output_safety.invalid_response": _s("operational", "error", "Assessment malformed"),
    "output_safety.validation_error": _s("operational", "error", "Assessment invalid"),
    "general_answer.started": _s("lifecycle", "ok", "General answer started", True),
    "general_answer.completed": _s("lifecycle", "ok", "General answer completed", True),
    "general_answer.budget_exceeded": _s("lifecycle", "blocked", "Budget exceeded"),
    "general_answer.error": _s("lifecycle", "error", "General answer failed"),
    "documentation_answer.started": _s("lifecycle", "ok", "Doc answer started", True),
    "documentation_answer.completed": _s("lifecycle", "ok", "Doc answer completed", True),
    "documentation_answer.budget_exceeded": _s("lifecycle", "blocked", "Budget exceeded"),
    "documentation_answer.error": _s("lifecycle", "error", "Doc answer failed"),
    "answer.ready": _s("lifecycle", "ok", "Answer returned"),
}

EVENT_NAMES: frozenset[str] = frozenset(get_args(TraceEventName))

#: Events that prove a request got past the deterministic input gate.
DOWNSTREAM_EVENTS: frozenset[str] = frozenset(
    name for name, spec in EVENT_SPEC.items() if spec.downstream
)

#: The gate event every run must contain.
GATE_EVENT = "policy_gate.checked"

#: The single event that records the routing decision.
INTENT_EVENT = "intent.classified"

#: Events that begin real work after routing, used for ordering assertions.
WORK_START_EVENTS: frozenset[str] = frozenset(
    {
        "retrieval.started",
        "general_answer.started",
        "documentation_answer.started",
    }
)


def spec_for(event: str) -> EventSpec:
    """Look up one event, raising rather than guessing."""
    try:
        return EVENT_SPEC[event]  # type: ignore[index]
    except KeyError as exc:
        raise UnknownTraceEvent(
            f"{event!r} is not in the trace contract; add it to TraceEventName and EVENT_SPEC"
        ) from exc


def domain_for(event: str) -> TraceDomain:
    """Domain carried on the JSONL line, from the registry rather than a prefix."""
    return spec_for(event).domain


__all__ = [
    "DOWNSTREAM_EVENTS",
    "EVENT_NAMES",
    "EVENT_SPEC",
    "GATE_EVENT",
    "INTENT_EVENT",
    "WORK_START_EVENTS",
    "EventSpec",
    "EventStatus",
    "TraceDomain",
    "TraceEventName",
    "UnknownTraceEvent",
    "domain_for",
    "spec_for",
]
