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
        if "intent" in self.url:
            self.events.append(f"intent:{name}")
            if isinstance(arguments, dict) and "sky" in str(arguments.get("question", "")).lower():
                return {
                    "intent": "general_question",
                    "confidence": 0.91,
                    "risk_flags": [],
                    "recommended_action": "answer_without_tools",
                    "needs_clarification": False,
                }
            if isinstance(arguments, dict) and "sql" in str(arguments.get("question", "")).lower():
                return {
                    "intent": "safe_sql_generation",
                    "confidence": 0.94,
                    "risk_flags": [],
                    "recommended_action": "generate_sql",
                    "needs_clarification": False,
                }
            return {
                "intent": "aggregate_definition",
                "confidence": 0.93,
                "risk_flags": [],
                "recommended_action": "search_tables",
                "needs_clarification": False,
            }
        if "catalog" in self.url:
            self.events.append(f"catalog:{name}")
            if name == "search_tables":
                return {
                    "candidates": [
                        {"table_name": "appointments"},
                    ],
                }
            return {
                "table_name": "appointments",
                "columns": [
                    {"name": "status", "safety_label": "safe_aggregate"},
                ],
            }
        if "sql-generation" in self.url:
            self.events.append(f"sql_generation:{name}")
            return {
                "sql": "SELECT status, COUNT(*) AS appointment_count FROM appointments GROUP BY status;",
                "tables": ["appointments"],
                "notes": ["Generated from mock schema."],
                "refused": False,
                "reason": None,
                "source": "claude_sql_generation",
                "is_dummy": True,
            }
        if "sql-validation" in self.url:
            self.events.append(f"sql_validation:{name}")
            return {
                "allowed": True,
                "reason": None,
                "normalized_sql": "SELECT status, COUNT(*) AS appointment_count FROM appointments GROUP BY status;",
                "tables": ["appointments"],
                "notes": ["SQL passed validation."],
                "source": "deterministic_sql_validation",
                "is_dummy": True,
            }
        raise AssertionError(f"Unexpected bridge URL: {self.url}")


class ConfigurableSqlBridge(FakeBridge):
    generated_result: dict[str, object] = {
        "sql": "SELECT status, COUNT(*) AS appointment_count FROM appointments GROUP BY status;",
        "tables": ["appointments"],
        "notes": [],
        "refused": False,
        "reason": None,
        "source": "claude_sql_generation",
        "is_dummy": True,
    }
    validation_result: object = {
        "allowed": True,
        "reason": None,
        "normalized_sql": "SELECT status, COUNT(*) AS appointment_count FROM appointments GROUP BY status",
        "tables": ["appointments"],
        "referenced_tables": ["appointments"],
        "referenced_columns": ["appointments.status"],
        "declared_tables": ["appointments"],
        "statement_type": "Select",
        "violations": [],
        "validator_version": "sqlglot_ast_v2",
        "sqlglot_version": "30.12.0",
        "notes": [],
        "source": "deterministic_sql_validation",
        "is_dummy": True,
    }

    async def call_tool(self, name: str, arguments: object) -> object:
        if "sql-generation" in self.url:
            self.events.append(f"sql_generation:{name}")
            return dict(self.generated_result)
        if "sql-validation" in self.url:
            self.events.append(f"sql_validation:{name}")
            if isinstance(self.validation_result, dict):
                return dict(self.validation_result)
            return self.validation_result
        return await super().call_tool(name, arguments)


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
        sql_generation_mcp_url="http://sql-generation.test/mcp",
        sql_validation_mcp_url="http://sql-validation.test/mcp",
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


@pytest.mark.asyncio
async def test_general_question_answers_without_catalog_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    FakeBridge.events = []
    client = FakeClient()
    monkeypatch.setattr(agent, "get_settings", lambda: _settings(str(tmp_path)))
    monkeypatch.setattr(agent, "MCPToolBridge", FakeBridge)
    monkeypatch.setattr(agent, "build_model_client", lambda _: client)

    result = await agent.answer_question("What color is the sky?")

    assert result.intent == "general_question"
    assert result.intent_confidence == 0.91
    assert result.used_tools == []
    assert result.answer == "catalog answer"
    assert FakeBridge.events == ["intent:classify_intent", "model:create"]
    assert client.messages.kwargs is not None
    assert client.messages.kwargs["tools"] == []


@pytest.mark.asyncio
async def test_safe_sql_request_searches_generates_and_validates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    FakeBridge.events = []
    monkeypatch.setattr(agent, "get_settings", lambda: _settings(str(tmp_path)))
    monkeypatch.setattr(agent, "MCPToolBridge", FakeBridge)

    result = await agent.answer_question("Write SQL to count appointments by status")

    assert result.intent == "safe_sql_generation"
    assert result.intent_confidence == 0.94
    assert result.used_tools == [
        "search_tables",
        "get_table_schema",
        "generate_sql",
        "validate_sql",
    ]
    assert "```sql" in result.answer
    assert "GROUP BY status" in result.answer
    assert FakeBridge.events == [
        "intent:classify_intent",
        "catalog:search_tables",
        "catalog:get_table_schema",
        "sql_generation:generate_sql",
        "sql_validation:validate_sql",
    ]


@pytest.mark.asyncio
async def test_sql_generator_refusal_stops_before_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ConfigurableSqlBridge.events = []
    ConfigurableSqlBridge.generated_result = {
        "sql": None,
        "tables": [],
        "notes": [],
        "refused": True,
        "reason": "unsafe_request",
        "source": "claude_sql_generation",
        "is_dummy": True,
    }
    monkeypatch.setattr(agent, "get_settings", lambda: _settings(str(tmp_path)))
    monkeypatch.setattr(agent, "MCPToolBridge", ConfigurableSqlBridge)

    result = await agent.answer_question("Write SQL to count appointments by status")

    assert "refused it: unsafe_request" in result.answer
    assert "validate_sql" not in result.used_tools
    assert "sql_validation:validate_sql" not in ConfigurableSqlBridge.events


@pytest.mark.asyncio
async def test_sql_validation_block_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ConfigurableSqlBridge.events = []
    ConfigurableSqlBridge.generated_result = {
        "sql": "SELECT status FROM appointments",
        "tables": ["appointments"],
        "notes": [],
        "refused": False,
        "reason": None,
        "source": "claude_sql_generation",
        "is_dummy": True,
    }
    ConfigurableSqlBridge.validation_result = {
        "allowed": False,
        "reason": "non_aggregate_sql",
        "normalized_sql": None,
        "tables": ["appointments"],
        "violations": [{"code": "non_aggregate_sql"}],
    }
    monkeypatch.setattr(agent, "get_settings", lambda: _settings(str(tmp_path)))
    monkeypatch.setattr(agent, "MCPToolBridge", ConfigurableSqlBridge)

    result = await agent.answer_question("Write SQL to count appointments by status")

    assert "blocked it: non_aggregate_sql" in result.answer
    assert "```sql" not in result.answer


@pytest.mark.asyncio
async def test_allowed_validation_without_normalized_sql_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ConfigurableSqlBridge.events = []
    ConfigurableSqlBridge.generated_result = {
        "sql": "SELECT COUNT(*) FROM appointments",
        "tables": ["appointments"],
        "notes": [],
        "refused": False,
        "reason": None,
        "source": "claude_sql_generation",
        "is_dummy": True,
    }
    ConfigurableSqlBridge.validation_result = {
        "allowed": True,
        "reason": None,
        "normalized_sql": None,
        "tables": ["appointments"],
        "violations": [],
    }
    monkeypatch.setattr(agent, "get_settings", lambda: _settings(str(tmp_path)))
    monkeypatch.setattr(agent, "MCPToolBridge", ConfigurableSqlBridge)

    result = await agent.answer_question("Write SQL to count appointments by status")

    assert "validation result was incomplete" in result.answer
    assert "```sql" not in result.answer
