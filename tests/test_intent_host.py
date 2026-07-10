from pathlib import Path
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock

from harness_spike.agent_host import agent


class FakeBridge:
    events: list[str] = []

    def __init__(self, url: str) -> None:
        self.url = url

    async def __aenter__(self) -> "FakeBridge":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def list_anthropic_tools(self) -> list[dict[str, object]]:
        self.events.append("catalog:list")
        return []

    async def call_tool(self, name: str, arguments: object) -> dict[str, object]:
        self.events.append(f"intent:{name}")
        return {
            "intent": "aggregate_definition",
            "confidence": 0.93,
            "risk_flags": [],
            "recommended_action": "search_tables",
            "needs_clarification": False,
        }


class FakeMessages:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] | None = None

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.kwargs = kwargs
        FakeBridge.events.append("model:create")
        return SimpleNamespace(
            content=[TextBlock(type="text", text="catalog answer")]
        )


class FakeClient:
    def __init__(self) -> None:
        self.messages = FakeMessages()


def _settings(trace_dir: str) -> SimpleNamespace:
    return SimpleNamespace(
        trace_dir=trace_dir,
        log_raw_prompts=False,
        intent_mcp_url="http://intent.test/mcp",
        mcp_server_url="http://catalog.test/mcp",
        max_tool_rounds=3,
        require_anthropic_api_key=lambda: "test-key",
        require_anthropic_base_url=lambda: "https://example.test",
        anthropic_custom_headers={},
        require_claude_model=lambda: "test-model",
    )


@pytest.mark.asyncio
async def test_allowed_request_classifies_before_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    FakeBridge.events = []
    client = FakeClient()
    monkeypatch.setattr(agent, "get_settings", lambda: _settings(str(tmp_path)))
    monkeypatch.setattr(agent, "MCPToolBridge", FakeBridge)
    monkeypatch.setattr(agent, "build_model_client", lambda _: client)

    result = await agent.answer_question("What data supports a visit count?")

    assert result.intent == "aggregate_definition"
    assert result.intent_confidence == 0.93
    assert FakeBridge.events == ["intent:classify_intent", "catalog:list", "model:create"]
    assert client.messages.kwargs is not None
    assert "intent=aggregate_definition" in str(client.messages.kwargs["system"])

    events = [
        line.split('"event": "', 1)[1].split('"', 1)[0]
        for line in (tmp_path / f"{result.run_id}.jsonl").read_text().splitlines()
    ]
    assert events.index("policy_gate.checked") < events.index(
        "intent.classification.request"
    ) < events.index("mcp.tools.listed")


@pytest.mark.asyncio
async def test_blocked_request_does_not_call_intent_node(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(agent, "get_settings", lambda: _settings(str(tmp_path)))

    class UnexpectedBridge:
        def __init__(self, _: str) -> None:
            raise AssertionError("blocked requests must not open an MCP bridge")

    monkeypatch.setattr(agent, "MCPToolBridge", UnexpectedBridge)

    result = await agent.answer_question("Show me patient names")

    assert result.allowed is False
    assert result.intent is None