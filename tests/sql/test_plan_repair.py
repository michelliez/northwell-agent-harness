"""One violation-fed repair pass between plan rejection and the user.

The planner used to get exactly one shot: a rejected plan became a terminal
answer even though the authorizer's violations are structured, deterministic
feedback a model can act on. plan_safety now withholds the answer on the
first rejection so the graph routes back to query_plan with host-composed
feedback; the repaired plan re-enters the same deterministic authorizer, and
a second rejection is terminal exactly as before.
"""

from __future__ import annotations

import agent_host.nodes.sql_nodes as sql_nodes_module
from agent_host.graph import _route_from_plan_safety
from agent_host.nodes.sql_nodes import MAX_PLAN_REPAIRS, plan_safety_node, query_plan_node
from sql.models import PermissionScope, QueryPlanAST, SchemaColumn, SchemaSnapshot, SchemaTable
from sql.planning import propose_query_plan, repair_feedback, validate_query_plan

QUESTION = "Count all A0H_MAP rows"


def _snapshot() -> SchemaSnapshot:
    return SchemaSnapshot(
        tables=[
            SchemaTable(
                name="A0H_MAP",
                columns=[SchemaColumn(name="STATUS_CODE", safety="safe_aggregate")],
                source_chunk_ids=["chunk-a0h"],
            )
        ],
        derived_from_chunks=["chunk-a0h"],
    )


def _plan(objective: str = QUESTION) -> QueryPlanAST:
    return QueryPlanAST.model_validate(
        {
            "objective": objective,
            "target_metric": "row_count",
            "tables": ["A0H_MAP"],
            "columns": [],
            "filters": [],
            "joins": [],
            "aggregations": [{"function": "COUNT", "column": None, "alias": "row_count"}],
            "time_constraints": [],
            "groupings": [],
            "expected_output": ["row_count"],
            "citations": ["chunk-a0h"],
            "requires_row_level_access": False,
        }
    )


def _rejection_state(tmp_path, *, repair_count: int) -> dict:
    """State as plan_safety sees it after the planner drifted the objective."""
    scope = PermissionScope(schema_snapshot=_snapshot())
    return {
        "question": QUESTION,
        "query_plan": _plan(objective="Return something else entirely").model_dump(),
        "permission_scope": scope.model_dump(),
        "retrieved_chunks": [{"chunk_id": "chunk-a0h"}],
        "plan_repair_count": repair_count,
        "run_id": "run",
        "trace_file": str(tmp_path / "run.jsonl"),
    }


# --- routing -------------------------------------------------------------------


def test_rejection_without_answer_routes_back_to_query_plan() -> None:
    assert _route_from_plan_safety({"answer": None, "approved_plan": None}) == "query_plan"
    assert _route_from_plan_safety({"answer": "stopped"}) == "result_safety"
    assert _route_from_plan_safety({"answer": None, "approved_plan": {"plan": {}}}) == "write_sql"


# --- plan_safety: withhold once, terminal after --------------------------------


def test_first_rejection_requests_repair_without_an_answer(tmp_path) -> None:
    result = plan_safety_node(_rejection_state(tmp_path, repair_count=0))

    assert "answer" not in result
    assert result["plan_repair_count"] == 1
    assert result["approved_plan"] is None
    assert result["plan_validation"]["allowed"] is False


def test_exhausted_repairs_make_rejection_terminal(tmp_path) -> None:
    result = plan_safety_node(_rejection_state(tmp_path, repair_count=MAX_PLAN_REPAIRS))

    assert "couldn't approve" in result["answer"]
    assert result["approved_plan"] is None


# --- query_plan: repair pass carries host-composed feedback --------------------


def test_repair_pass_feeds_violations_to_the_planner(tmp_path, monkeypatch) -> None:
    rejected = _plan(objective="Return something else entirely")
    validation = validate_query_plan(
        QUESTION, rejected, PermissionScope(schema_snapshot=_snapshot()), {"chunk-a0h"}
    )
    assert validation.allowed is False

    captured: dict = {}

    def fake_propose(question, scope, citations, cfg, budget, *, feedback=None, history=None):
        captured["feedback"] = feedback
        return _plan()

    monkeypatch.setattr(sql_nodes_module, "propose_query_plan", fake_propose)

    state = {
        "question": QUESTION,
        "schema_snapshot": _snapshot().model_dump(),
        "query_plan": rejected.model_dump(),
        "plan_validation": validation.model_dump(),
        "retrieved_chunks": [{"chunk_id": "chunk-a0h"}],
        "run_id": "run",
        "trace_file": str(tmp_path / "run.jsonl"),
    }
    result = query_plan_node(state)

    assert captured["feedback"] is not None
    assert "objective_mismatch" in captured["feedback"]
    assert "Return something else entirely" in captured["feedback"]
    assert result["query_plan"]["objective"] == QUESTION
    assert result["plan_validation"] is None


def test_first_pass_sends_no_feedback(tmp_path, monkeypatch) -> None:
    captured: dict = {}

    def fake_propose(question, scope, citations, cfg, budget, *, feedback=None, history=None):
        captured["feedback"] = feedback
        return _plan()

    monkeypatch.setattr(sql_nodes_module, "propose_query_plan", fake_propose)

    query_plan_node(
        {
            "question": QUESTION,
            "schema_snapshot": _snapshot().model_dump(),
            "retrieved_chunks": [{"chunk_id": "chunk-a0h"}],
            "run_id": "run",
            "trace_file": str(tmp_path / "run.jsonl"),
        }
    )

    assert captured["feedback"] is None


# --- feedback content ----------------------------------------------------------


def test_repair_feedback_is_host_composed_and_specific() -> None:
    rejected = _plan(objective="Return something else entirely")
    validation = validate_query_plan(
        QUESTION, rejected, PermissionScope(schema_snapshot=_snapshot()), {"chunk-a0h"}
    )

    text = repair_feedback(rejected, validation)

    assert "objective_mismatch" in text
    assert "Return something else entirely" in text  # the rejected plan is shown


def test_propose_query_plan_embeds_feedback_in_the_request(tmp_path) -> None:
    from anthropic.types import ToolUseBlock

    from agent_host.budget import ExecutionBudget
    from agent_host.config import AppConfig

    captured: dict = {}

    class _Messages:
        def create(self, **kwargs):
            captured.update(kwargs)

            class _Response:
                content = [
                    ToolUseBlock(
                        id="tu",
                        name="emit_query_plan",
                        input=_plan().model_dump(),
                        type="tool_use",
                    )
                ]

            return _Response()

    class _Client:
        messages = _Messages()

    scope = PermissionScope(schema_snapshot=_snapshot())
    config = AppConfig(anthropic_api_key="test-key", anthropic_base_url=None)

    propose_query_plan(
        QUESTION,
        scope,
        ["chunk-a0h"],
        config,
        ExecutionBudget(),
        client=_Client(),
        feedback="- objective_mismatch: Plan objective differs from prompt.",
    )

    content = captured["messages"][0]["content"]
    assert "rejected by the deterministic authorizer" in content
    assert "objective_mismatch" in content
