"""LangGraph typed state for the simplified pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, TypedDict

from agent_host.budget import ExecutionBudget


def _append(existing: list, updates: list) -> list:
    """Reducer that appends updates to existing list without overwriting."""
    return existing + updates


@dataclass
class AgentContext:
    """Non-checkpointed services shared by every node in one request."""

    budget: ExecutionBudget


class AgentState(TypedDict, total=False):
    # Input
    question: str
    history: list[dict]

    # Intent classification
    intent: str | None
    intent_confidence: float | None
    recommended_action: str | None
    risk_flags: list[str]

    # Permissions (stub for future role/user lookup)
    permissions: dict[str, bool]

    # Retrieval
    retrieved_chunks: list[dict]  # serialized RetrievedChunk
    schema_snapshot: dict | None  # serialized SchemaSnapshot

    # SQL workflow
    query_plan: dict | None  # serialized QueryPlan
    generated_sql: str | None
    validation_result: dict | None  # serialized SqlValidationResult
    execution_status: str | None  # "not_configured" | None
    repair_count: int
    repair_hint: str | None

    # Output
    citations: Annotated[list[str], _append]
    output_safety_assessment: dict | None  # serialized output safety classification

    # Answer and lifecycle
    answer: str | None
    policy_blocked: bool
    policy_reason: str | None
    clarification_count: int

    # Trace bookkeeping
    run_id: str
    trace_file: str | None
    started_at: float


def make_initial_state(question: str, run_id: str, started_at: float) -> AgentState:
    """Build a fully-initialized state for the start of a new turn."""
    return AgentState(
        question=question,
        history=[],
        intent=None,
        intent_confidence=None,
        recommended_action=None,
        risk_flags=[],
        permissions={},
        retrieved_chunks=[],
        schema_snapshot=None,
        query_plan=None,
        generated_sql=None,
        validation_result=None,
        execution_status=None,
        repair_count=0,
        repair_hint=None,
        citations=[],
        output_safety_assessment=None,
        answer=None,
        policy_blocked=False,
        policy_reason=None,
        clarification_count=0,
        run_id=run_id,
        trace_file=None,
        started_at=started_at,
    )
