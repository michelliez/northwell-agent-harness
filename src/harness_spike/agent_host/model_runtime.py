"""Bounded model/tool loop independent of request routing."""

from __future__ import annotations

from typing import Any, cast

from anthropic import Anthropic
from anthropic.types import MessageParam, TextBlock, ToolParam, ToolUseBlock

from harness_spike.agent_host.budget import (
    BudgetExceeded,
    ExecutionBudget,
    ModelStopError,
)
from harness_spike.agent_host.mcp_bridge import MCPToolBridge
from harness_spike.agent_host.model_call import call_model
from harness_spike.agent_host.responses import (
    budget_exceeded_response,
    content_blocked_response,
    final_answer_response,
    max_rounds_response,
    model_stop_response,
    tool_contract_error_response,
    unauthorized_tool_response,
)
from harness_spike.agent_host.schemas import AskResponse
from harness_spike.agent_host.tool_execution import execute_tool_uses
from harness_spike.agent_host.tool_registry import ToolContractError
from harness_spike.agent_host.trace_logger import TraceLogger
from harness_spike.config import Settings
from harness_spike.policy.screen import ContentScreenBlocked


async def run_agent_loop(
    question: str,
    client: Anthropic,
    model: str,
    tools: list[ToolParam],
    mcp: MCPToolBridge,
    settings: Settings,
    trace: TraceLogger,
    system: str,
    allowed_tools: frozenset[str],
    budget: ExecutionBudget,
    *,
    server: str = "catalog",
) -> AskResponse:
    """Run a bounded model loop over one explicit host-owned tool server."""
    messages: list[MessageParam] = [{"role": "user", "content": question}]
    used_tools: list[str] = []
    model_call_number = 0

    while True:
        model_call_number += 1
        try:
            response = call_model(
                client,
                model,
                messages,
                tools,
                trace,
                settings,
                model_call_number,
                system,
                budget=budget,
            )
        except BudgetExceeded as exc:
            return budget_exceeded_response(trace, exc, used_tools)
        except ModelStopError as exc:
            return model_stop_response(trace, exc, used_tools)
        tool_uses = get_tool_uses(response)
        if not tool_uses:
            return final_answer_response(response, trace, used_tools)
        try:
            budget.reserve_round()
        except BudgetExceeded as exc:
            if exc.reason == "max_rounds":
                return max_rounds_response(settings, trace, tool_uses, used_tools)
            return budget_exceeded_response(trace, exc, used_tools)

        unauthorized = [
            tool_use.name for tool_use in tool_uses if tool_use.name not in allowed_tools
        ]
        if unauthorized:
            trace.record(
                "tool.blocked",
                reason="intent_tool_scope",
                tools=unauthorized,
                allowed_tools=sorted(allowed_tools),
            )
            return unauthorized_tool_response(trace, used_tools, unauthorized)

        messages.append(
            {"role": "assistant", "content": cast(Any, assistant_content(response.content))}
        )
        try:
            tool_results = await execute_tool_uses(
                mcp,
                tool_uses,
                trace,
                model_call_number,
                used_tools,
                budget=budget,
                server=server,
            )
        except ContentScreenBlocked as exc:
            return content_blocked_response(trace, exc)
        except ToolContractError as exc:
            return tool_contract_error_response(trace, exc, used_tools)
        except BudgetExceeded as exc:
            return budget_exceeded_response(trace, exc, used_tools)
        messages.append({"role": "user", "content": cast(Any, tool_results)})
        try:
            budget.check_context(messages)
        except BudgetExceeded as exc:
            return budget_exceeded_response(trace, exc, used_tools)


def get_tool_uses(response: Any) -> list[ToolUseBlock]:
    return [block for block in response.content if isinstance(block, ToolUseBlock)]


def assistant_content(blocks: list[Any]) -> list[dict[str, Any]]:
    """Strip SDK-only fields before replaying assistant content."""
    content: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            content.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            content.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
    return content
