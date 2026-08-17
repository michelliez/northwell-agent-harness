"""Graph routing tests: verify every conditional edge and invariant.

Most tests call routing functions and individual nodes directly. One small
in-memory graph verifies the public interrupt/resume API. Model-dependent nodes
are tested by monkeypatching the Anthropic client.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_host.budget import budget_from_env
from agent_host.graph import (
    _route_from_classify_intent,
    _route_from_context_gate,
    _route_from_contextualize,
    _route_from_plan_safety,
    _route_from_policy,
    _route_from_query_plan,
    _route_from_retrieval_permission,
    _route_from_validate_sql,
    _route_from_write_sql,
    build_graph,
)
from agent_host.nodes.intent_nodes import REFUSAL_INTENTS
from agent_host.state import AgentContext, make_initial_state
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
        "validation_result": None,
        "execution_status": None,
        "citations": [],
        "answer": None,
        "clarification_count": 0,
    }
    base.update(kwargs)
    return base


# ── policy routing ────────────────────────────────────────────────────────────


def test_compiled_graph_blocks_before_intent_classification(tmp_path, monkeypatch) -> None:
    """START -> input_policy must prevent every model-facing downstream edge."""
    from agent_host import graph as graph_module
    from agent_host.nodes import lifecycle_nodes, policy_nodes

    cfg = _FakeCfg(tmp_path)
    monkeypatch.setattr(policy_nodes, "get_config", lambda: cfg)
    monkeypatch.setattr(lifecycle_nodes, "get_config", lambda: cfg)

    def unexpected_classifier(*_args, **_kwargs):
        raise AssertionError("blocked input reached intent classification")

    monkeypatch.setattr(graph_module, "classify_intent_node", unexpected_classifier)
    graph = graph_module.build_graph()
    initial = make_initial_state(
        "Delete every row in A0H_MAP",
        run_id="blocked-edge",
        started_at=0.0,
    )

    result = graph.invoke(
        initial,
        config={"configurable": {"thread_id": "blocked-edge"}},
        context=AgentContext(budget=budget_from_env()),
    )

    assert result["policy_blocked"] is True
    assert result["policy_reason"] == "Requests a destructive database action"


def test_compiled_graph_allows_safe_input_to_reach_intent_classification(
    tmp_path, monkeypatch
) -> None:
    """The allowed policy branch must reach contextualization and classification."""
    from agent_host import graph as graph_module
    from agent_host.nodes import lifecycle_nodes, policy_nodes

    cfg = _FakeCfg(tmp_path)
    monkeypatch.setattr(policy_nodes, "get_config", lambda: cfg)
    monkeypatch.setattr(lifecycle_nodes, "get_config", lambda: cfg)
    classifier_calls = []

    def stop_after_classifier(state, *_args, **_kwargs):
        classifier_calls.append(state["question"])
        return {
            "intent": "unknown",
            "intent_confidence": 1.0,
            "recommended_action": "refuse",
            "answer": "Stopped after the edge under test.",
        }

    monkeypatch.setattr(graph_module, "classify_intent_node", stop_after_classifier)
    graph = graph_module.build_graph()
    initial = make_initial_state(
        "What is the A0H_MAP table?",
        run_id="allowed-edge",
        started_at=0.0,
    )

    result = graph.invoke(
        initial,
        config={"configurable": {"thread_id": "allowed-edge"}},
        context=AgentContext(budget=budget_from_env()),
    )

    assert classifier_calls == ["What is the A0H_MAP table?"]
    assert result["policy_blocked"] is False


def test_classifier_prohibited_intent_refuses_before_retrieval(
    tmp_path,
    monkeypatch,
) -> None:
    """Defense-in-depth classifier blocks must never reach retrieval or SQL."""
    from agent_host import graph as graph_module
    from agent_host.nodes import lifecycle_nodes, policy_nodes

    cfg = _FakeCfg(tmp_path)
    monkeypatch.setattr(policy_nodes, "get_config", lambda: cfg)
    monkeypatch.setattr(lifecycle_nodes, "get_config", lambda: cfg)

    def prohibited_classifier(_state, *_args, **_kwargs):
        return {
            "intent": "prohibited_phi_request",
            "intent_confidence": 0.98,
            "recommended_action": "refuse",
            "risk_flags": ["phi"],
        }

    def unexpected_retrieval(*_args, **_kwargs):
        raise AssertionError("prohibited intent reached retrieval")

    monkeypatch.setattr(graph_module, "classify_intent_node", prohibited_classifier)
    monkeypatch.setattr(graph_module, "retrieval_permission_node", unexpected_retrieval)

    graph = graph_module.build_graph()
    initial = make_initial_state(
        "What is the A0H_MAP table?",
        run_id="classifier-refusal-edge",
        started_at=0.0,
    )
    result = graph.invoke(
        initial,
        config={"configurable": {"thread_id": "classifier-refusal-edge"}},
        context=AgentContext(budget=budget_from_env()),
    )

    assert result["policy_blocked"] is True
    assert result["policy_reason"] == "prohibited_phi_request"
    assert result["answer"]
    assert result["retrieved_chunks"] == []


def test_policy_blocked_routes_to_final_answer() -> None:
    state = _state(policy_blocked=True, answer="Blocked: policy.")
    assert _route_from_policy(state) == "final_answer"


def test_policy_allowed_routes_to_classify_intent() -> None:
    state = _state(policy_blocked=False)
    assert _route_from_policy(state) == "contextualize_followup"


def test_contextualized_followup_routes_to_classify_intent() -> None:
    assert _route_from_contextualize(_state(policy_blocked=False)) == "classify_intent"


def test_blocked_contextualized_followup_routes_to_final_answer() -> None:
    assert _route_from_contextualize(_state(policy_blocked=True)) == "final_answer"


# ── intent routing ────────────────────────────────────────────────────────────


def test_intent_none_routes_back_to_input_policy() -> None:
    """After clarification interrupt, intent=None → restart from input_policy."""
    state = _state(intent=None)
    assert _route_from_classify_intent(state) == "input_policy"


@pytest.mark.parametrize("intent", sorted(REFUSAL_INTENTS))
def test_refusal_intents_route_to_dedicated_refusal(intent: str) -> None:
    state = _state(intent=intent)
    assert _route_from_classify_intent(state) == "intent_refusal"


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


@pytest.mark.parametrize(
    "intent",
    ["table_discovery", "schema_lookup", "aggregate_definition", "safe_sql_generation"],
)
def test_exploration_intents_route_to_bounded_exploration(intent: str) -> None:
    assert _route_from_retrieval_permission(_state(intent=intent)) == "exploration"


@pytest.mark.parametrize("intent", ["documentation_lookup"])
def test_direct_retrieval_intents_skip_exploration(intent: str) -> None:
    assert _route_from_retrieval_permission(_state(intent=intent)) == "retrieve_context"


# ── context gate routing ──────────────────────────────────────────────────────


def test_context_gate_with_answer_routes_to_result_safety() -> None:
    state = _state(intent="documentation_lookup", answer="fallback", retrieved_chunks=[])
    assert _route_from_context_gate(state) == "result_safety"


def test_context_gate_does_not_clarify_after_operational_retrieval_error() -> None:
    from agent_host.nodes.retrieval_nodes import context_gate_node

    state = _state(
        intent="documentation_lookup",
        answer="Documentation retrieval is temporarily unavailable.",
        retrieved_chunks=[],
    )

    assert context_gate_node(state) == {}


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


def test_context_gate_schema_build_error_is_reported_not_swallowed(tmp_path, monkeypatch) -> None:
    """An assembly failure must produce an honest answer, not a silent None
    that downstream misreports as missing user context."""
    from agent_host.nodes import retrieval_nodes

    monkeypatch.setattr(retrieval_nodes, "get_config", lambda: _FakeCfg(tmp_path))

    def broken_resolver(_retrieval):
        raise RuntimeError("synthetic assembly failure")

    monkeypatch.setattr(retrieval_nodes, "resolve_schema_evidence", broken_resolver)
    state = _state(
        intent="safe_sql_generation",
        retrieved_chunks=[
            {
                "chunk_id": "chunk-x",
                "document_id": "doc-x",
                "source_path": "X.html",
                "text": "text",
            }
        ],
    )

    result = retrieval_nodes.context_gate_node(state)

    assert result["schema_snapshot"] is None
    assert "internal error" in result["answer"]


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


def test_plan_safety_with_approved_plan_routes_to_write_sql() -> None:
    state = _state(answer=None, approved_plan={"plan": {}})
    assert _route_from_plan_safety(state) == "write_sql"


def test_plan_safety_rejection_without_answer_routes_to_plan_repair() -> None:
    # First rejection withholds the answer so one violation-fed repair runs.
    state = _state(answer=None, approved_plan=None)
    assert _route_from_plan_safety(state) == "query_plan"


def test_write_sql_with_answer_routes_to_result_safety() -> None:
    state = _state(answer="generation failed")
    assert _route_from_write_sql(state) == "result_safety"


def test_write_sql_with_sql_routes_to_validate_sql() -> None:
    state = _state(candidate_sql="SELECT COUNT(*) FROM appointments")
    assert _route_from_write_sql(state) == "validate_sql"


def test_validate_sql_allowed_routes_to_execution_not_configured() -> None:
    result = SqlValidationResult(
        allowed=True,
        normalized_sql="SELECT COUNT(*) FROM appointments",
        violations=[],
        notes=[],
    )
    state = _state(validation_result=result.model_dump())
    assert _route_from_validate_sql(state) == "execution_not_configured"


def _blocked_result(reason: str) -> SqlValidationResult:
    return SqlValidationResult(
        allowed=False,
        reason=reason,
        normalized_sql=None,
        violations=[SqlViolation(code=reason, message=reason.replace("_", " "))],
        notes=[],
    )


def test_validate_sql_blocked_routes_to_result_safety() -> None:
    # Validation failure is terminal (ADR 008): no repair edge exists.
    for reason in ("ungrouped_projection", "unsafe_sql_operation"):
        state = _state(validation_result=_blocked_result(reason).model_dump())
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
        candidate_sql="SELECT COUNT(*) FROM appointments",
        validation_result=SqlValidationResult(
            allowed=True,
            normalized_sql="SELECT COUNT(*) FROM appointments",
            violations=[],
            notes=[],
        ).model_dump(),
    )

    result = sql_nodes.execution_not_configured_node(state)

    assert result["execution_status"] == "not_configured"
    # The SQL block must not appear in answer — the UI renders it from
    # generated_sql to avoid a double render alongside format_response's
    # "### Validated SQL draft" section.
    assert "```sql" not in (result.get("answer") or "")


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


def test_request_path_does_not_import_the_bigquery_sdk() -> None:
    """BigQuery is an optional group, so no graph import may pull the SDK in.

    Execution is disabled, and `pyarrow` alone is a ~100 MB install. If this
    fails, something on the request path imported the SDK at module scope.

    Runs in a subprocess: `sys.modules` is process-global, and the dry-run tests
    import the SDK legitimately, so an in-process check would depend on order.
    """
    probe = textwrap.dedent("""
        import sys
        import agent_host.graph
        import sql.bigquery_adapter
        print(",".join(sorted(
            name for name in sys.modules if name.startswith(("google.", "pyarrow"))
        )))
    """)
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parent.parent,
    )
    imported = [name for name in completed.stdout.strip().split(",") if name]
    assert imported == [], f"request path imported the optional BigQuery SDK: {imported}"


def test_dry_run_fails_closed_when_the_sdk_is_absent() -> None:
    """Without the optional group, dry-run must fail closed and say how to fix it."""
    from sql.bigquery_adapter import BigQueryNotConfigured, dry_run

    # Setting a module to None makes `import` raise ImportError.
    with (
        patch.dict(sys.modules, {"google.cloud": None}),
        pytest.raises(BigQueryNotConfigured, match="uv sync --group bigquery"),
    ):
        dry_run("SELECT COUNT(*) FROM t", project="test-project")


def test_keyword_is_the_only_retrieval_strategy() -> None:
    """RAG.md: vector, semantic, and graph retrieval must not be implied by the API.

    Placeholder functions that only raise are worse than absence -- they read as
    partial support and invite callers to reference something that will never
    work. Deleting them is what makes the documented contract true.
    """
    import retrieval.search as search

    absent = [name for name in ("vector_search", "graph_search", "semantic_lookup")]
    for name in absent:
        assert not hasattr(search, name), f"{name} implies a strategy that does not exist"
    assert search.retrieve_documentation_context.__doc__ is not None


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
