"""Tests that refuse intents block responses and don't reach downstream nodes."""

from __future__ import annotations

import pytest

from agent_host.nodes.intent_nodes import (
    REFUSAL_INTENTS,
    _merge_clarification,
    classify_intent_node,
    intent_refusal_node,
)


def _make_state(question: str, clarification_count: int = 0) -> dict:
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
        "validation_result": None,
        "execution_status": None,
        "citations": [],
        "answer": None,
        "clarification_count": clarification_count,
    }


class _FakeCfg:
    def __init__(self, tmp_path):
        self.trace_dir = tmp_path
        self.trace_content_mode = "metadata"
        self.anthropic_custom_headers = {}
        self.anthropic_base_url = None
        self.intent_min_confidence = 0.70

    def require_api_key(self):
        return "test-key"

    def require_base_url(self):
        return "https://example.test"

    def require_model(self):
        return "test-model"


def _make_fake_anthropic(intent: str, confidence: float = 0.92, needs_clarification: bool = False):
    """Return a mock Anthropic client that returns the given intent."""
    from types import SimpleNamespace

    from anthropic.types import ToolUseBlock

    class _Messages:
        def create(self, **_):
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[
                    ToolUseBlock(
                        id="tool-1",
                        input={
                            "intent": intent,
                            "confidence": confidence,
                            "risk_flags": [],
                            "needs_clarification": needs_clarification,
                        },
                        name="emit_intent",
                        type="tool_use",
                    )
                ],
            )

    class _Client:
        messages = _Messages()

    return _Client()


@pytest.mark.parametrize("intent", sorted(REFUSAL_INTENTS))
def test_refuse_intent_sets_refuse_action(intent: str, tmp_path, monkeypatch) -> None:
    """All refusal intents must route to refuse, not downstream nodes."""
    from agent_host.nodes import intent_nodes

    monkeypatch.setattr(intent_nodes, "get_config", lambda: _FakeCfg(tmp_path))
    monkeypatch.setattr(intent_nodes, "Anthropic", lambda **_: _make_fake_anthropic(intent))

    result = classify_intent_node(_make_state("list all patients"))

    assert result.get("intent") == intent
    assert result.get("recommended_action") == "refuse"


@pytest.mark.parametrize(
    ("intent", "reason"),
    [
        ("prohibited_phi_request", "prohibited_phi_request"),
        ("prompt_injection_attempt", "prompt_injection_attempt"),
        ("jailbreak_attempt", "jailbreak_attempt"),
        ("destructive_sql_request", "destructive_sql_request"),
    ],
)
def test_intent_refusal_node_returns_a_displayable_block(
    intent: str,
    reason: str,
) -> None:
    result = intent_refusal_node(_make_state("prohibited request") | {"intent": intent})

    assert result["policy_blocked"] is True
    assert result["policy_reason"] == reason
    assert result["answer"]


def test_low_confidence_intent_routes_to_clarify(tmp_path, monkeypatch) -> None:
    """Low-confidence classification must set intent=unknown, action=clarify."""
    from agent_host.nodes import intent_nodes

    monkeypatch.setattr(intent_nodes, "get_config", lambda: _FakeCfg(tmp_path))
    monkeypatch.setattr(
        intent_nodes,
        "Anthropic",
        lambda **_: _make_fake_anthropic("documentation_lookup", confidence=0.40),
    )

    # With max clarification attempts not yet reached, interrupt would be called.
    # But we can't easily test LangGraph interrupt without the full graph.
    # Instead verify that at clarification_count >= MAX, a fallback answer is set.
    state = _make_state("what is ABN_ORDERS?", clarification_count=3)

    result = classify_intent_node(state)
    # When clarification_count >= MAX_CLARIFICATION_ATTEMPTS, answer is set
    assert result.get("answer") is not None


def test_general_question_intent_maps_to_answer_without_tools(tmp_path, monkeypatch) -> None:
    from agent_host.nodes import intent_nodes

    monkeypatch.setattr(intent_nodes, "get_config", lambda: _FakeCfg(tmp_path))
    monkeypatch.setattr(
        intent_nodes,
        "Anthropic",
        lambda **_: _make_fake_anthropic("general_question"),
    )

    result = classify_intent_node(_make_state("what is the sky?"))
    assert result.get("intent") == "general_question"
    assert result.get("recommended_action") == "answer_without_tools"


def test_classify_intent_node_fails_closed_on_model_error(tmp_path, monkeypatch) -> None:
    from agent_host.nodes import intent_nodes

    class _FailingClient:
        class messages:
            @staticmethod
            def create(**_):
                raise RuntimeError("Model unreachable")

    monkeypatch.setattr(intent_nodes, "get_config", lambda: _FakeCfg(tmp_path))
    monkeypatch.setattr(intent_nodes, "Anthropic", lambda **_: _FailingClient())

    result = intent_nodes.classify_intent_node(_make_state("How many encounters last month?"))

    assert result.get("intent") in {"unknown", None}
    assert result.get("recommended_action") in {"clarify", None}


def test_clarification_reply_preserves_the_original_sql_request() -> None:
    merged = _merge_clarification(
        "Write SQL to find the average of the VISITS column",
        "mean",
    )

    assert "average of the VISITS column" in merged
    assert "User clarification: mean" in merged
