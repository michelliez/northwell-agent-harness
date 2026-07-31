from __future__ import annotations

from agent_host.trace_contract import EVENT_SPEC

# The viewer used to keep its own event map. It drifted: after the pipeline moved
# to a graph it still classified MCP-era names like `intent.classification.request`
# that no node emits, so nearly every event in a real run rendered as "unknown".
# Classification now derives from the one contract, and an unmapped event means
# the event is genuinely off-contract rather than merely unregistered here.

NODE_TYPE_LABELS: dict[str, str] = {
    "policy": "Policy Gate",
    "intent": "Intent",
    "retrieval": "Retrieval",
    "sql": "SQL",
    "lifecycle": "Answer",
    "operational": "Operational",
    "unknown": "Unknown",
}

#: Derived from the contract; kept for callers that want the whole mapping.
NODE_TYPE_MAP: dict[str, str] = {name: spec.domain for name, spec in EVENT_SPEC.items()}


def classify_event(event_name: str) -> str:
    """Map an event to its domain, or 'unknown' if it is off-contract."""
    spec = EVENT_SPEC.get(event_name)  # type: ignore[arg-type]
    return spec.domain if spec is not None else "unknown"


def event_status(event_name: str) -> str:
    """Report whether an event is a normal step, a deliberate stop, or a failure."""
    spec = EVENT_SPEC.get(event_name)  # type: ignore[arg-type]
    return spec.status if spec is not None else "unknown"
