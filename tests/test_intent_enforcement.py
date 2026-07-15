from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic.types import Message, TextBlock, Usage

from harness_spike.agent_host.agent import answer_question


def _fake_settings(tmp_path):
    return SimpleNamespace(
        trace_dir=str(tmp_path),
        log_raw_prompts=False,
        intent_mcp_url="http://localhost:8002/mcp",
        mcp_server_url="http://localhost:8000/mcp",
        sql_generation_mcp_url="http://localhost:8003/mcp",
        sql_validation_mcp_url="http://localhost:8004/mcp",
        max_tool_rounds=3,
        require_claude_model=lambda: "test-model",
        require_anthropic_api_key=lambda: "test-key",
        require_anthropic_base_url=lambda: "https://example.test",
        anthropic_custom_headers={},
    )


def _fake_bridge(call_tool_return: dict) -> AsyncMock:
    bridge = AsyncMock()
    bridge.call_tool = AsyncMock(return_value=call_tool_return)
    bridge.__aenter__ = AsyncMock(return_value=bridge)
    bridge.__aexit__ = AsyncMock(return_value=False)
    return bridge


# A prompt that clears the deterministic policy gate so we can reach
# the intent classifier.
SAFE_PROMPT = "How many encounters happened last month?"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "risk_flags"),
    [
        ("patient_specific_request", ["patient_identifiers", "individual_records"]),
        ("policy_probe", ["policy_manipulation"]),
        ("unsupported_sql_request", ["patient_identifiers"]),
    ],
)
async def test_refuse_intent_blocks_response(
    intent: str,
    risk_flags: list[str],
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All three refuse intents must produce allowed=False with no downstream calls."""
    intent_result = {
        "intent": intent,
        "confidence": 0.91,
        "risk_flags": risk_flags,
        "recommended_action": "refuse",
        "needs_clarification": False,
    }
    bridge = _fake_bridge(intent_result)

    monkeypatch.setattr(
        "harness_spike.agent_host.agent.get_settings",
        lambda: _fake_settings(tmp_path),
    )

    with patch("harness_spike.agent_host.agent.MCPToolBridge", return_value=bridge):
        result = await answer_question(SAFE_PROMPT)

    assert result.allowed is False
    assert result.intent == intent
    assert result.policy_reason is not None
    assert "intent_classifier_refused" in result.policy_reason


@pytest.mark.asyncio
async def test_refuse_intent_makes_no_catalog_calls(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the intent classifier refuses, only one MCP bridge is opened (intent only)."""
    intent_result = {
        "intent": "patient_specific_request",
        "confidence": 0.95,
        "risk_flags": ["patient_identifiers"],
        "recommended_action": "refuse",
        "needs_clarification": False,
    }
    bridge = _fake_bridge(intent_result)

    monkeypatch.setattr(
        "harness_spike.agent_host.agent.get_settings",
        lambda: _fake_settings(tmp_path),
    )

    with patch("harness_spike.agent_host.agent.MCPToolBridge", return_value=bridge) as MockBridge:
        await answer_question(SAFE_PROMPT)

    # MCPToolBridge should be constructed exactly once (for the intent classifier).
    # If it were called again, catalog or model tools were reached — that is wrong.
    assert MockBridge.call_count == 1


@pytest.mark.asyncio
async def test_refuse_intent_records_blocked_trace_event(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request.blocked trace event must be written when the classifier refuses."""
    import json

    intent_result = {
        "intent": "policy_probe",
        "confidence": 0.88,
        "risk_flags": ["policy_manipulation"],
        "recommended_action": "refuse",
        "needs_clarification": False,
    }
    bridge = _fake_bridge(intent_result)

    monkeypatch.setattr(
        "harness_spike.agent_host.agent.get_settings",
        lambda: _fake_settings(tmp_path),
    )

    with patch("harness_spike.agent_host.agent.MCPToolBridge", return_value=bridge):
        result = await answer_question(SAFE_PROMPT)

    trace_path = result.trace_file
    events = [json.loads(line) for line in open(trace_path)]
    event_types = [e["event"] for e in events]

    assert "request.blocked" in event_types

    blocked_event = next(e for e in events if e["event"] == "request.blocked")
    assert blocked_event["reason"] == "intent_classifier_refused"
    assert blocked_event["intent"] == "policy_probe"


@pytest.mark.asyncio
async def test_safe_intent_is_not_refused(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A safe general_question intent must NOT produce allowed=False."""
    intent_result = {
        "intent": "general_question",
        "confidence": 0.92,
        "risk_flags": [],
        "recommended_action": "answer_without_tools",
        "needs_clarification": False,
    }
    bridge = _fake_bridge(intent_result)

    # general_question routes to answer_general_question(), which calls the
    # Anthropic model directly (no second MCP bridge). Mock the model client.
    fake_response = Message(
        id="msg-test",
        content=[TextBlock(text="This is a general answer.", type="text")],
        model="test-model",
        role="assistant",
        stop_reason="end_turn",
        type="message",
        usage=Usage(input_tokens=10, output_tokens=5),
    )
    fake_messages = MagicMock()
    fake_messages.create = MagicMock(return_value=fake_response)
    fake_client = MagicMock()
    fake_client.messages = fake_messages

    monkeypatch.setattr(
        "harness_spike.agent_host.agent.get_settings",
        lambda: _fake_settings(tmp_path),
    )
    monkeypatch.setattr(
        "harness_spike.agent_host.agent.Anthropic",
        lambda **_: fake_client,
    )

    with patch("harness_spike.agent_host.agent.MCPToolBridge", return_value=bridge):
        result = await answer_question(SAFE_PROMPT)

    assert result.allowed is True
