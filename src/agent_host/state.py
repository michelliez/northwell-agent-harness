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
    original_question: str
    question: str
    history: list[dict]
    conversation_turns: list[dict]
    question_was_contextualized: bool

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
    permission_scope: dict | None  # serialized host-owned PermissionScope
    query_plan: dict | None  # serialized untrusted QueryPlanAST
    plan_validation: dict | None  # serialized PlanValidationResult
    approved_plan: dict | None  # serialized ApprovedQueryPlan
    compiled_query: dict | None  # serialized CompiledQuery
    candidate_sql: str | None  # the draft under validation, and the final draft
    query_parameters: list[dict]
    validation_result: dict | None  # serialized SqlValidationResult
    dry_run_result: dict | None  # serialized trusted DryRunResult
    cost_gate_result: dict | None  # serialized CostGateResult
    approval_token: str | None
    execution_status: str | None  # "not_configured" | None

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


def make_initial_state(
    question: str,
    run_id: str,
    started_at: float,
    *,
    conversation_turns: list[dict] | None = None,
) -> AgentState:
    """Build a fully-initialized state for the start of a new turn."""
    return AgentState(
        original_question=question,
        question=question,
        history=[],
        conversation_turns=list(conversation_turns or []),
        question_was_contextualized=False,
        intent=None,
        intent_confidence=None,
        recommended_action=None,
        risk_flags=[],
        permissions={},
        retrieved_chunks=[],
        schema_snapshot=None,
        permission_scope=None,
        query_plan=None,
        plan_validation=None,
        approved_plan=None,
        compiled_query=None,
        candidate_sql=None,
        query_parameters=[],
        validation_result=None,
        dry_run_result=None,
        cost_gate_result=None,
        approval_token=None,
        execution_status=None,
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
