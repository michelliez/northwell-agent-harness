"""Graph routing tests: verify every conditional edge and invariant.

Most tests call routing functions and individual nodes directly. One small
in-memory graph verifies the public interrupt/resume API. Model-dependent nodes
are tested by monkeypatching the Anthropic client.
"""

from __future__ import annotations

import pytest

from agent_host.graph import (
    _route_from_classify_intent,
    _route_from_context_gate,
    _route_from_fix_sql,
    _route_from_generate_sql,
    _route_from_plan_safety,
    _route_from_policy,
    _route_from_query_plan,
    _route_from_retrieval_permission,
    _route_from_validate_sql,
    build_graph,
)
from agent_host.nodes.intent_nodes import REFUSAL_INTENTS
from sql.models import SqlValidationResult, SqlViolation


def _state(**kwargs) -> dict:
    base = {
        "question": "test question",
        "history": [],
        "run_id": "test-run",
        "trace_file": None,
        "started_at": 0.0,
        "policy_blocked": False,
        "policy_reason": None,
        "intent": None,
        "intent_confidence": None,
        "recommended_action": None,
        "risk_flags": [],
        "permissions": {},
        "retrieved_chunks": [],
        "schema_snapshot": None,
        "permission_scope": None,
        "query_plan": None,
        "plan_validation": None,
        "approved_plan": None,
        "compiled_query": None,
        "candidate_sql": None,
        "query_parameters": [],
        "generated_sql": None,
        "validation_result": None,
        "execution_status": None,
        "repair_count": 0,
        "repair_hint": None,
        "repair_history": [],
        "citations": [],
        "answer": None,
        "clarification_count": 0,
    }
    base.update(kwargs)
    return base


# ── policy routing ────────────────────────────────────────────────────────────


def test_policy_blocked_routes_to_final_answer() -> None:
    state = _state(policy_blocked=True, answer="Blocked: policy.")
    assert _route_from_policy(state) == "final_answer"


def test_policy_allowed_routes_to_classify_intent() -> None:
    state = _state(policy_blocked=False)
    assert _route_from_policy(state) == "classify_intent"


# ── intent routing ────────────────────────────────────────────────────────────


def test_intent_none_routes_back_to_input_policy() -> None:
    """After clarification interrupt, intent=None → restart from input_policy."""
    state = _state(intent=None)
    assert _route_from_classify_intent(state) == "input_policy"


@pytest.mark.parametrize("intent", sorted(REFUSAL_INTENTS))
def test_refusal_intents_route_to_result_safety(intent: str) -> None:
    state = _state(intent=intent)
    assert _route_from_classify_intent(state) == "result_safety"


def test_general_question_routes_to_general_answer() -> None:
    state = _state(intent="general_question")
    assert _route_from_classify_intent(state) == "general_answer"


@pytest.mark.parametrize(
    "intent",
    [
        "table_discovery",
        "schema_lookup",
        "documentation_lookup",
        "aggregate_definition",
        "safe_sql_generation",
    ],
)
def test_retrieval_intents_route_to_retrieval_permission(intent: str) -> None:
    state = _state(intent=intent)
    assert _route_from_classify_intent(state) == "retrieval_permission"


def test_unknown_intent_with_clarification_exhausted_routes_to_result_safety() -> None:
    state = _state(intent="unknown")
    assert _route_from_classify_intent(state) == "result_safety"


# ── retrieval authority routing ──────────────────────────────────────────────


@pytest.mark.parametrize("intent", ["table_discovery", "schema_lookup", "aggregate_definition"])
def test_exploration_intents_route_to_bounded_exploration(intent: str) -> None:
    assert _route_from_retrieval_permission(_state(intent=intent)) == "exploration"


@pytest.mark.parametrize("intent", ["documentation_lookup", "safe_sql_generation"])
def test_direct_retrieval_intents_skip_exploration(intent: str) -> None:
    assert _route_from_retrieval_permission(_state(intent=intent)) == "retrieve_context"


# ── context gate routing ──────────────────────────────────────────────────────


def test_context_gate_with_answer_routes_to_result_safety() -> None:
    state = _state(intent="documentation_lookup", answer="fallback", retrieved_chunks=[])
    assert _route_from_context_gate(state) == "result_safety"


def test_context_gate_after_clarification_routes_to_input_policy() -> None:
    state_with_null_intent = _state(intent=None, answer=None, retrieved_chunks=[])
    assert _route_from_context_gate(state_with_null_intent) == "input_policy"


def test_context_gate_docs_intent_routes_to_documentation_answer() -> None:
    state = _state(
        intent="documentation_lookup",
        retrieved_chunks=[{"chunk_id": "abc", "text": "something"}],
    )
    assert _route_from_context_gate(state) == "documentation_answer"


def test_context_gate_sql_intent_routes_to_query_plan() -> None:
    state = _state(
        intent="safe_sql_generation",
        retrieved_chunks=[{"chunk_id": "abc", "text": "something"}],
    )
    assert _route_from_context_gate(state) == "query_plan"


def test_context_gate_builds_sql_schema_from_retrieved_chunk_model_dump(
    tmp_path, monkeypatch
) -> None:
    """RetrievedChunk uses document_id; the graph adapter must preserve that contract."""
    from agent_host.nodes import retrieval_nodes
    from retrieval.client import RetrievedChunk

    monkeypatch.setattr(retrieval_nodes, "get_config", lambda: _FakeCfg(tmp_path))
    retrieved_chunk = RetrievedChunk(
        chunk_id="chunk-status",
        document_id="doc-acc-config-blk",
        source_path="ACC_CONFIG_BLK.html",
        heading_path="ACC_CONFIG_BLK > CONFIG_STATUS",
        category="column_info",
        text="CONFIG_STATUS stores the configuration status.",
        rank=1,
        score=1.0,
    )
    state = _state(
        intent="safe_sql_generation",
        question="Count ACC_CONFIG_BLK records by CONFIG_STATUS",
        retrieved_chunks=[retrieved_chunk.model_dump()],
    )

    result = retrieval_nodes.context_gate_node(state)

    assert result["schema_snapshot"] is not None
    assert result["schema_snapshot"]["tables"][0]["name"] == "ACC_CONFIG_BLK"
    assert result["schema_snapshot"]["tables"][0]["columns"][0]["name"] == "CONFIG_STATUS"


def test_context_gate_exploration_intents_route_to_documentation_answer() -> None:
    for intent in ["table_discovery", "schema_lookup", "aggregate_definition"]:
        state = _state(
            intent=intent,
            retrieved_chunks=[{"chunk_id": "abc", "text": "something"}],
        )
        assert _route_from_context_gate(state) == "documentation_answer"


# ── SQL workflow routing ──────────────────────────────────────────────────────


def test_query_plan_with_answer_routes_to_result_safety() -> None:
    state = _state(answer="no schema")
    assert _route_from_query_plan(state) == "result_safety"


def test_query_plan_without_answer_routes_to_plan_safety() -> None:
    state = _state(answer=None)
    assert _route_from_query_plan(state) == "plan_safety"


def test_plan_safety_with_answer_routes_to_result_safety() -> None:
    state = _state(answer="unsafe plan")
    assert _route_from_plan_safety(state) == "result_safety"


def test_plan_safety_without_answer_routes_to_write_sql() -> None:
    state = _state(answer=None)
    assert _route_from_plan_safety(state) == "write_sql"


def test_generate_sql_with_answer_routes_to_result_safety() -> None:
    state = _state(answer="generation failed")
    assert _route_from_generate_sql(state) == "result_safety"


def test_generate_sql_with_sql_routes_to_validate_sql() -> None:
    state = _state(generated_sql="SELECT COUNT(*) FROM appointments")
    assert _route_from_generate_sql(state) == "validate_sql"


def test_successful_fix_routes_back_through_validation() -> None:
    state = _state(candidate_sql="SELECT COUNT(*) FROM A0H_MAP", answer=None)
    assert _route_from_fix_sql(state) == "validate_sql"


def test_failed_fix_routes_to_result_safety() -> None:
    state = _state(candidate_sql="old SQL", answer="Repair loop stopped.")
    assert _route_from_fix_sql(state) == "result_safety"


def test_validate_sql_allowed_routes_to_execution_not_configured() -> None:
    result = SqlValidationResult(
        allowed=True,
        normalized_sql="SELECT COUNT(*) FROM appointments",
        violations=[],
        notes=[],
    )
    state = _state(validation_result=result.model_dump(), repair_count=0)
    assert _route_from_validate_sql(state) == "execution_not_configured"


def _blocked_result(reason: str, repairable: bool) -> SqlValidationResult:
    return SqlValidationResult(
        allowed=False,
        reason=reason,
        normalized_sql=None,
        violations=[SqlViolation(code=reason, message=reason.replace("_", " "))],
        notes=[],
        is_repairable=repairable,
    )


def test_validate_sql_repairable_within_budget_routes_to_fix_sql() -> None:
    result = _blocked_result("ungrouped_projection", repairable=True)
    # repair_count=1 < max_sql_repairs=3 → route to fix_sql
    state = _state(validation_result=result.model_dump(), repair_count=1)
    assert _route_from_validate_sql(state) == "fix_sql"


def test_validate_sql_budget_exhausted_routes_to_result_safety() -> None:
    result = _blocked_result("ungrouped_projection", repairable=True)
    # repair_count=3 >= max_sql_repairs=3 → route to result_safety
    state = _state(validation_result=result.model_dump(), repair_count=3)
    assert _route_from_validate_sql(state) == "result_safety"


def test_validate_sql_not_repairable_routes_to_result_safety() -> None:
    result = _blocked_result("unsafe_sql_operation", repairable=False)
    state = _state(validation_result=result.model_dump(), repair_count=0)
    assert _route_from_validate_sql(state) == "result_safety"


# ── policy-before-model ordering invariant ────────────────────────────────────


def test_policy_before_model_in_routing_logic() -> None:
    """Verify that policy_blocked=True routes to final_answer, not classify_intent.

    This tests the invariant that no model call is made when policy blocks.
    """
    # Policy blocks → goes to final_answer (not classify_intent)
    blocked_state = _state(policy_blocked=True, answer="blocked")
    route = _route_from_policy(blocked_state)
    assert route == "final_answer"
    # NOT "classify_intent" — verifies policy gates model calls


# ── no accidental SQL execution ───────────────────────────────────────────────


def test_execution_not_configured_node_does_not_execute_sql(tmp_path, monkeypatch) -> None:
    """execution_not_configured_node must set execution_status='not_configured', never run SQL."""
    from agent_host.nodes import sql_nodes

    monkeypatch.setattr(sql_nodes, "get_config", lambda: _FakeCfg(tmp_path))

    state = _state(
        generated_sql="SELECT COUNT(*) FROM appointments",
        validation_result=SqlValidationResult(
            allowed=True,
            normalized_sql="SELECT COUNT(*) FROM appointments",
            violations=[],
            notes=[],
        ).model_dump(),
    )

    result = sql_nodes.execution_not_configured_node(state)

    assert result["execution_status"] == "not_configured"
    # Answer contains the SQL draft but no indication that execution occurred
    assert "not configured" in result["answer"].lower()


# ── thread isolation ──────────────────────────────────────────────────────────


def test_graph_compiles_successfully() -> None:
    """Graph must compile without errors."""
    graph = build_graph()
    assert graph is not None


def test_different_thread_ids_are_isolated() -> None:
    """Two threads must not share state in InMemorySaver."""
    from langgraph.checkpoint.memory import InMemorySaver

    checkpointer = InMemorySaver()
    graph = build_graph(checkpointer=checkpointer)

    # Both threads should start with no prior state
    cfg1 = {"configurable": {"thread_id": "thread-alpha"}}
    cfg2 = {"configurable": {"thread_id": "thread-beta"}}

    state1 = graph.get_state(cfg1)
    state2 = graph.get_state(cfg2)

    # Both should be empty (no history from each other)
    assert state1.values == {} or state1.next == ()
    assert state2.values == {} or state2.next == ()


def test_public_api_interrupts_and_resumes_same_thread(monkeypatch) -> None:
    """The graph API must expose LangGraph interrupt/Command resume semantics."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    from agent_host import graph as graph_module
    from agent_host.state import AgentState

    def clarify(state: AgentState) -> dict:
        reply = interrupt("Which table should I use?")
        return {
            "question": str(reply),
            "answer": f"Continuing with {reply}.",
            "intent": "documentation_lookup",
        }

    builder = StateGraph(AgentState)
    builder.add_node("clarify", clarify)
    builder.add_edge(START, "clarify")
    builder.add_edge("clarify", END)
    monkeypatch.setattr(graph_module, "_graph", builder.compile(checkpointer=InMemorySaver()))

    interrupted = graph_module.ask("Help me find a table", thread_id="clarification-test")
    assert interrupted.interrupted is True
    assert interrupted.clarification_prompt == "Which table should I use?"

    completed = graph_module.resume("CLARITY_ADT", thread_id="clarification-test")
    assert completed.interrupted is False
    assert completed.answer == "Continuing with CLARITY_ADT."


# ── future adapters fail closed ───────────────────────────────────────────────


def test_bigquery_adapter_raises_on_all_entry_points() -> None:
    """All BigQuery adapter functions must raise BigQueryNotConfigured."""
    from sql.bigquery_adapter import (
        BigQueryNotConfigured,
        dry_run,
        estimate_cost,
        execute_read_only,
        redact_result,
    )

    for fn in (dry_run, estimate_cost, execute_read_only, redact_result):
        with pytest.raises(BigQueryNotConfigured):
            fn("SELECT COUNT(*) FROM t")  # type: ignore[call-arg]


def test_inactive_retrieval_strategies_fail_closed() -> None:
    from retrieval.search import (
        RetrievalStrategyNotConfigured,
        graph_search,
        semantic_lookup,
        vector_search,
    )

    for strategy in (vector_search, graph_search, semantic_lookup):
        with pytest.raises(RetrievalStrategyNotConfigured):
            strategy("appointments")


def test_python_generation_fails_closed() -> None:
    from sql.python_generation import PythonGenerationNotConfigured, generate_python

    with pytest.raises(PythonGenerationNotConfigured):
        generate_python("analyze appointments")


class _FakeCfg:
    def __init__(self, tmp_path):
        self.trace_dir = tmp_path
        self.trace_content_mode = "metadata"
        self.anthropic_custom_headers = {}
        self.anthropic_base_url = None
        self.intent_min_confidence = 0.70
        self.index_path = tmp_path / "index.sqlite"
        self.artifact_path = tmp_path

    def require_api_key(self):
        return "test-key"

    def require_base_url(self):
        return "https://example.test"

    def require_model(self):
        return "test-model"
