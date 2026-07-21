from types import SimpleNamespace

import pytest
from anthropic.types import ToolUseBlock

from agent_host.tool_registry import tools_for_intent
from mcp_servers import intent


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
    assert "aggregate_definition, search_tables" in str(client.messages.kwargs["system"])


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


def test_classify_intent_accepts_general_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _response(
        {
            "intent": "general_question",
            "confidence": 0.91,
            "risk_flags": [],
            "recommended_action": "answer_without_tools",
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

    result = intent.classify_intent("What color is the sky?")

    assert result["intent"] == "general_question"
    assert result["recommended_action"] == "answer_without_tools"


def test_classify_intent_accepts_documentation_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _response(
        {
            "intent": "documentation_lookup",
            "confidence": 0.94,
            "risk_flags": [],
            "recommended_action": "retrieve_documentation",
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

    result = intent.classify_intent("What do the docs say about appointment status?")

    assert result["intent"] == "documentation_lookup"
    assert result["recommended_action"] == "retrieve_documentation"


def test_classify_intent_forces_refusal_action_for_sensitive_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _response(
        {
            "intent": "patient_specific_request",
            "confidence": 0.95,
            "risk_flags": ["patient_level"],
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

    result = intent.classify_intent("Which patient had the visit?")

    assert result["recommended_action"] == "refuse"
    assert result["needs_clarification"] is False


def test_classify_intent_fails_closed_on_incoherent_safe_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _response(
        {
            "intent": "schema_lookup",
            "confidence": 0.95,
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

    result = intent.classify_intent("What columns are in encounters?")

    assert result["intent"] == "unknown"
    assert result["recommended_action"] == "clarify"
    assert "incoherent_intent_action" in result["risk_flags"]


def test_route_tool_scope_is_host_owned() -> None:
    assert tools_for_intent("schema_lookup") == {"get_table_schema"}
    assert tools_for_intent("patient_specific_request") == set()
    assert tools_for_intent("documentation_lookup") == {"search_docs", "get_doc_chunk"}


def test_classify_intent_rejects_missing_or_duplicate_tool_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        require_anthropic_api_key=lambda: "test-key",
        require_anthropic_base_url=lambda: "https://example.test",
        anthropic_custom_headers={},
        require_claude_model=lambda: "test-model",
    )
    monkeypatch.setattr(intent, "get_settings", lambda: settings)
    monkeypatch.setattr(intent, "Anthropic", lambda **_: FakeClient(SimpleNamespace(content=[])))

    with pytest.raises(RuntimeError, match="exactly one"):
        intent.classify_intent("Which tables are relevant?")


def test_intent_tool_contract_is_closed_and_versioned() -> None:
    assert intent.INTENT_PROMPT_VERSION == "v5"
    assert intent.INTENT_TOOL["name"] == "emit_intent"
    assert intent.INTENT_TOOL["input_schema"]["additionalProperties"] is False
    assert set(intent.INTENT_TOOL["input_schema"]["required"]) == {
        "intent",
        "confidence",
        "risk_flags",
        "recommended_action",
        "needs_clarification",
    }


@pytest.mark.asyncio
async def test_intent_mcp_exposes_only_classifier_tool() -> None:
    tools = await intent.mcp.list_tools()
    assert [tool.name for tool in tools] == ["classify_intent"]


def test_v2_prompt_covers_observed_adversarial_failure_modes() -> None:
    assert (
        "Apply this order when a request contains more than one intent"
        in intent.CLASSIFIER_SYSTEM_PROMPT
    )
    assert "force an intent label" in intent.CLASSIFIER_SYSTEM_PROMPT
    assert "metadata with patient-level output" in intent.CLASSIFIER_SYSTEM_PROMPT
    assert "safe_sql_generation" in intent.CLASSIFIER_SYSTEM_PROMPT
    assert "Which documents can I look at for admissions info?" in intent.CLASSIFIER_SYSTEM_PROMPT
    assert "generate_sql" in intent.CLASSIFIER_SYSTEM_PROMPT
    assert "general_question" in intent.CLASSIFIER_SYSTEM_PROMPT
    assert "answer_without_tools" in intent.CLASSIFIER_SYSTEM_PROMPT
    assert '"Show me the schema" means unknown, clarify' in intent.CLASSIFIER_SYSTEM_PROMPT
