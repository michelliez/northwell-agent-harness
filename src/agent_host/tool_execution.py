"""Host-owned bounded MCP discovery and execution primitives."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from anthropic.types import ToolParam, ToolUseBlock

from agent_host.budget import BudgetExceeded, ExecutionBudget
from agent_host.mcp_bridge import MCPToolBridge
from agent_host.model_call import tool_names
from agent_host.responses import record_content_screen
from agent_host.tool_registry import (
    ToolContractError,
    contract_for_tool,
    validate_live_inventory,
)
from agent_host.trace_logger import TraceLogger
from policy.screen import (
    ContentScreenBlocked,
    ContentSurface,
    screen_content,
)


async def call_workflow_tool(
    mcp: MCPToolBridge,
    name: str,
    arguments: dict[str, Any],
    trace: TraceLogger,
    used_tools: list[str],
    *,
    budget: ExecutionBudget,
    server: str,
) -> Any:
    """Call one host-authorized tool and validate every boundary."""
    contract = contract_for_tool(name, server=server)
    validated_arguments = contract.validate_input(arguments)
    budget.reserve_tool_call(name, validated_arguments)
    trace.record("tool.selected", name=name, input=validated_arguments)
    try:
        result = await asyncio.wait_for(
            mcp.call_tool(name, validated_arguments),
            timeout=budget.mcp_call_timeout_seconds,
        )
    except TimeoutError as exc:
        raise BudgetExceeded("mcp_call_timeout") from exc
    used_tools.append(name)
    budget.accept_tool_result(result)
    result = contract.validate_result(result)
    screen_result = screen_content(result, ContentSurface.TOOL_RESULT)
    record_content_screen(
        trace,
        screen_result,
        location=f"tool.result:{name}",
        used_tools=used_tools,
    )
    if not screen_result.allowed:
        raise ContentScreenBlocked(screen_result, f"tool.result:{name}", used_tools)
    trace.record("tool.result", name=name, result=result)
    return result


async def discover_tools(
    mcp: MCPToolBridge,
    trace: TraceLogger,
    *,
    server: str,
    mcp_url: str,
    required_tools: frozenset[str],
    log_raw_prompts: bool,
) -> list[ToolParam]:
    """Validate live discovery against the host-owned server contract."""
    tools = await mcp.list_anthropic_tools()
    safe_live_tools: list[ToolParam] = []
    for tool in tools:
        name = str(tool.get("name", "unknown")) if isinstance(tool, dict) else "unknown"
        screen_result = screen_content(tool, ContentSurface.TOOL_METADATA)
        record_content_screen(trace, screen_result, location=f"tool.metadata:{name}")
        if not screen_result.allowed:
            if name in required_tools:
                raise ContentScreenBlocked(screen_result, f"tool.metadata:{name}")
            continue
        safe_live_tools.append(tool)

    try:
        safe_tools = validate_live_inventory(
            safe_live_tools,
            server=server,
            required_tools=required_tools,
        )
    except ToolContractError:
        trace.record(
            "mcp.contract.failed",
            server=server,
            required_tools=sorted(required_tools),
        )
        raise

    trace.record(
        "mcp.tools.listed",
        mcp_url=mcp_url,
        tools=safe_tools if log_raw_prompts else tool_names(safe_tools),
    )
    return safe_tools


async def execute_tool_uses(
    mcp: MCPToolBridge,
    tool_uses: list[ToolUseBlock],
    trace: TraceLogger,
    round_number: int,
    used_tools: list[str],
    *,
    budget: ExecutionBudget,
    server: str,
) -> list[dict[str, str]]:
    """Execute model-selected tools within one explicit server scope."""
    tool_results = []
    for tool_use in tool_uses:
        contract = contract_for_tool(tool_use.name, server=server)
        validated_input = contract.validate_input(tool_use.input)
        budget.reserve_tool_call(tool_use.name, validated_input)
        trace.record(
            "tool.selected",
            round=round_number,
            name=tool_use.name,
            input=validated_input,
            tool_use_id=tool_use.id,
        )
        try:
            tool_result = await asyncio.wait_for(
                mcp.call_tool(tool_use.name, validated_input),
                timeout=budget.mcp_call_timeout_seconds,
            )
        except TimeoutError as exc:
            raise BudgetExceeded("mcp_call_timeout") from exc
        used_tools.append(tool_use.name)
        budget.accept_tool_result(tool_result)
        tool_result = contract.validate_result(tool_result)
        screen_result = screen_content(tool_result, ContentSurface.TOOL_RESULT)
        record_content_screen(
            trace,
            screen_result,
            location=f"tool.result:{tool_use.name}",
            used_tools=used_tools,
        )
        if not screen_result.allowed:
            raise ContentScreenBlocked(
                screen_result,
                f"tool.result:{tool_use.name}",
                used_tools,
            )
        trace.record("tool.result", round=round_number, name=tool_use.name, result=tool_result)
        tool_results.append(
            {
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": json.dumps(tool_result),
            }
        )
    return tool_results
