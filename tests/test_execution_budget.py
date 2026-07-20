from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock, ToolUseBlock

from harness_spike.agent_host.budget import BudgetExceeded, ExecutionBudget
from harness_spike.agent_host.model_runtime import run_agent_loop
from harness_spike.agent_host.trace_logger import TraceLogger
from harness_spike.agent_host.workflows.legacy_mock import fetch_candidate_schemas


def test_budget_caps_total_and_per_tool_calls() -> None:
    budget = ExecutionBudget(max_tool_calls=2, max_calls_per_tool=1)
    budget.reserve_tool_call("search_tables", {"question": "appointments"})
    budget.reserve_tool_call("get_table_schema", {"table_name": "appointments"})

    with pytest.raises(BudgetExceeded, match="max_tool_calls"):
        budget.reserve_tool_call("search_tables", {"question": "again"})


def test_budget_exclusively_owns_payload_size_limits() -> None:
    budget = ExecutionBudget(
        max_input_bytes=32,
        max_tool_result_bytes=32,
        max_context_bytes=32,
    )
    oversized = {"value": "x" * 100}

    with pytest.raises(BudgetExceeded, match="max_input_bytes"):
        budget.reserve_tool_call("search_tables", oversized)

    with pytest.raises(BudgetExceeded, match="max_tool_result_bytes"):
        budget.accept_tool_result(oversized)

    with pytest.raises(BudgetExceeded, match="max_context_bytes"):
        budget.check_context(oversized)


def test_budget_caps_candidate_schema_fanout(tmp_path: Path) -> None:
    class Catalog:
        calls: list[str] = []

        async def call_tool(self, name: str, arguments: object) -> dict[str, object]:
            self.calls.append(str(arguments))
            return {"table_name": arguments["table_name"], "columns": []}  # type: ignore[index]

    catalog = Catalog()
    budget = ExecutionBudget(max_candidate_schemas=1)

    schemas = asyncio.run(
        fetch_candidate_schemas(
            catalog,  # type: ignore[arg-type]
            {
                "candidates": [
                    {"table_name": "appointments"},
                    {"table_name": "encounters"},
                ]
            },
            TraceLogger(str(tmp_path)),
            [],
            budget=budget,
        )
    )

    assert len(schemas) == 1
    assert len(catalog.calls) == 1


class _ModelMessages:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls = 0

    def create(self, **_: object) -> object:
        self.calls += 1
        return self.response


class _ModelClient:
    def __init__(self, response: object) -> None:
        self.messages = _ModelMessages(response)


class _ToolBridge:
    calls: list[str] = []

    async def call_tool(self, name: str, arguments: object) -> dict[str, object]:
        self.calls.append(name)
        return {"candidates": []}


def _settings(trace_dir: str) -> SimpleNamespace:
    return SimpleNamespace(
        trace_dir=trace_dir,
        log_raw_prompts=False,
        max_tool_rounds=3,
        max_model_calls=4,
        max_tool_calls=12,
        max_calls_per_tool=6,
        max_input_bytes=16_000,
        max_tool_result_bytes=32_000,
        max_context_bytes=128_000,
        max_wall_seconds=60.0,
        mcp_call_timeout_seconds=10.0,
        model_max_tokens=300,
    )


@pytest.mark.asyncio
async def test_multiple_tool_uses_obey_total_call_budget(tmp_path: Path) -> None:
    response = SimpleNamespace(
        content=[
            ToolUseBlock(
                type="tool_use",
                id="one",
                name="search_tables",
                input={"question": "appointments"},
            ),
            ToolUseBlock(
                type="tool_use",
                id="two",
                name="search_tables",
                input={"question": "encounters"},
            ),
        ],
        stop_reason="tool_use",
    )
    client = _ModelClient(response)
    bridge = _ToolBridge()
    settings = _settings(str(tmp_path))
    budget = ExecutionBudget(max_tool_calls=1, max_calls_per_tool=2)

    result = await run_agent_loop(
        question="Which tables are relevant?",
        client=client,  # type: ignore[arg-type]
        model="test-model",
        tools=[],
        mcp=bridge,  # type: ignore[arg-type]
        settings=settings,
        trace=TraceLogger(str(tmp_path)),
        system="",
        allowed_tools=frozenset({"search_tables"}),
        budget=budget,
    )

    assert result.allowed is False
    assert result.policy_reason == "execution_budget_exceeded:max_tool_calls"
    assert bridge.calls == ["search_tables"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stop_reason", "policy_reason"),
    [("max_tokens", "model_max_tokens"), ("refusal", "model_refused")],
)
async def test_explicit_model_stop_states_fail_closed(
    stop_reason: str,
    policy_reason: str,
    tmp_path: Path,
) -> None:
    response = SimpleNamespace(
        content=[TextBlock(type="text", text="partial answer")],
        stop_reason=stop_reason,
    )
    client = _ModelClient(response)
    result = await run_agent_loop(
        question="Which tables are relevant?",
        client=client,  # type: ignore[arg-type]
        model="test-model",
        tools=[],
        mcp=_ToolBridge(),  # type: ignore[arg-type]
        settings=_settings(str(tmp_path)),
        trace=TraceLogger(str(tmp_path)),
        system="",
        allowed_tools=frozenset(),
        budget=ExecutionBudget(),
    )

    assert result.allowed is False
    assert result.policy_reason == policy_reason
    assert result.answer != "partial answer"
