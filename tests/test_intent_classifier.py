from types import SimpleNamespace

import pytest
from anthropic.types import ToolUseBlock

from harness_spike.mcp_servers import intent


def _response(payload: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            ToolUseBlock(
                id="tool-1",
                input=payload,
                name="emit_intent",
                type="tool_use",
            )
        ]
    )


class FakeMessages:
    def __init__(self, response: SimpleNamespace) -> None:
        self.response = response
        self.kwargs: dict[str, object] | None = None

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.kwargs = kwargs
        return self.response


class FakeClient:
    def __init__(self, response: SimpleNamespace) -> None:
        self.messages = FakeMessages(response)


def test_classify_intent_returns_validated_result(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _response(
        {
            "intent": "aggregate_definition",
            "confidence": 0.93,
            "risk_flags": [],
            "recommended_action": "search_tables",
            "needs_clarification": False,
        }
    )
    client = FakeClient(response)
    monkeypatch.setattr(intent, "Anthropic", lambda **_: client)
    monkeypatch.setattr(
        intent,
        "get_settings",
        lambda: SimpleNamespace(
            require_anthropic_api_key=lambda: "test-key",
            require_anthropic_base_url=lambda: "https://example.test",
            anthropic_custom_headers={},
            require_claude_model=lambda: "test-model",
        ),
    )

    result = intent.classify_intent("What data supports a visit count?")

    assert result["intent"] == "aggregate_definition"
    assert result["recommended_action"] == "search_tables"
    assert client.messages.kwargs is not None
    assert client.messages.kwargs["tool_choice"] == {
        "type": "tool",
        "name": "emit_intent",
    }
    assert "aggregate_definition, search_tables" in str(
        client.messages.kwargs["system"]
    )


def test_classify_intent_fails_closed_on_low_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _response(
        {
            "intent": "table_discovery",
            "confidence": 0.40,
            "risk_flags": [],
            "recommended_action": "search_tables",
            "needs_clarification": False,
        }
    )
    monkeypatch.setattr(intent, "Anthropic", lambda **_: FakeClient(response))
    monkeypatch.setattr(
        intent,
        "get_settings",
        lambda: SimpleNamespace(
            require_anthropic_api_key=lambda: "test-key",
            require_anthropic_base_url=lambda: "https://example.test",
            anthropic_custom_headers={},
            require_claude_model=lambda: "test-model",
        ),
    )

    result = intent.classify_intent("Help me explore the catalog")

    assert result == {
        "intent": "unknown",
        "confidence": 0.4,
        "risk_flags": ["low_confidence"],
        "recommended_action": "clarify",
        "needs_clarification": True,
    }
