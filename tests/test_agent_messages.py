"""Tests for graph-level policy routing (replaces model_runtime.assistant_content tests)."""

from __future__ import annotations

from agent_host.nodes.policy_nodes import input_policy_node


def _make_state(question: str) -> dict:
    return {
        "question": question,
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
        "query_plan": None,
        "generated_sql": None,
        "validation_result": None,
        "execution_status": None,
        "repair_count": 0,
        "repair_hint": None,
        "citations": [],
        "answer": None,
        "clarification_count": 0,
    }


def test_input_policy_blocks_patient_identifier_request(tmp_path, monkeypatch) -> None:
    """input_policy_node must block patient identifier requests before any model call."""
    from agent_host.nodes import policy_nodes

    monkeypatch.setattr(
        policy_nodes,
        "get_config",
        lambda: _FakeCfg(tmp_path),
    )

    state = _make_state("list all patient names from the encounters table")
    result = input_policy_node(state)

    assert result["policy_blocked"] is True
    assert result["answer"]


def test_input_policy_allows_safe_request(tmp_path, monkeypatch) -> None:
    """input_policy_node must not block aggregate-only requests."""
    from agent_host.nodes import policy_nodes

    monkeypatch.setattr(
        policy_nodes,
        "get_config",
        lambda: _FakeCfg(tmp_path),
    )

    state = _make_state("how many encounters were there last month?")
    result = input_policy_node(state)

    assert result.get("policy_blocked") is False


class _FakeCfg:
    def __init__(self, tmp_path):
        self.trace_dir = tmp_path
        self.trace_content_mode = "metadata"
        self.anthropic_custom_headers = {}
        self.anthropic_base_url = None
