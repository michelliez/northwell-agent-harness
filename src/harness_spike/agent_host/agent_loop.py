from __future__ import annotations

import json
from typing import Any, cast

from anthropic import Anthropic
from anthropic.types import MessageParam, TextBlock, ToolParam, ToolUseBlock

from harness_spike.agent_host.mcp_bridge import MCPToolBridge
from harness_spike.agent_host.schemas import AskResponse
from harness_spike.agent_host.trace_logger import TraceLogger
from harness_spike.config import Settings
from harness_spike.gates import policy_gate


#Entire agent loop
async def run_agent_loop(
    question: str,
    client: Anthropic,
    model: str,
    tools: list[ToolParam],
    mcp: MCPToolBridge,
    settings: Settings,
    trace: TraceLogger,
    system: str,
) -> AskResponse:
    messages: list[MessageParam] = [{"role": "user", "content": question}]
    used_tools: list[str] = []
    model_call_number = 0
    tool_rounds_used = 0

    while True:
        model_call_number += 1
        response = call_model(
            client=client,
            model=model,
            messages=messages,
            tools=tools,
            trace=trace,
            settings=settings,
            round_number=model_call_number,
            system=system,
        )

        tool_uses = get_tool_uses(response)


        if not tool_uses:
            return final_answer_response(response, trace, used_tools)

        if tool_rounds_used >= settings.max_tool_rounds:
            return max_rounds_response(settings, trace, tool_uses, used_tools)

        append_assistant_response(messages, response)

        tool_rounds_used += 1
        tool_results = await execute_tool_uses(
            mcp=mcp,
            tool_uses=tool_uses,
            trace=trace,
            round_number=model_call_number,
            used_tools=used_tools,
        )

        append_tool_results(messages, tool_results)






#Helpers
def blocked_response_if_needed(
    question: str,
    trace: TraceLogger,
) -> AskResponse | None:
    gate_result = policy_gate(question)
    trace.record("policy_gate.checked", result=gate_result)

    if gate_result["allowed"]:
        return None

    answer = (
        "I can't help with that request because it is blocked by the "
        f"policy gate: {gate_result['reason']}."
    )

    trace.record(
        "request.blocked",
        reason=gate_result["reason"],
        matched_term=gate_result["matched_term"],
    )

    return AskResponse(
        answer=answer,
        used_tools=[],
        run_id=trace.run_id,
        trace_file=str(trace.path),
        allowed=False,
        policy_reason=gate_result["reason"],
        matched_term=gate_result["matched_term"],
    )


async def discover_tools(
    mcp: MCPToolBridge,
    settings: Settings,
    trace: TraceLogger,
) -> list[ToolParam]:
    tools = await mcp.list_anthropic_tools()
    trace.record(
        "mcp.tools.listed",
        mcp_url=settings.mcp_server_url,
        tools=tools if settings.log_raw_prompts else tool_names(tools),
    )
    return tools

def call_model(
    client: Anthropic,
    model: str,
    messages: list[MessageParam],
    tools: list[ToolParam],
    trace: TraceLogger,
    settings: Settings,
    round_number: int,
    system: str,
):
    trace.record(
        "model.request",
        round=round_number,
        model=model,
        messages=messages if settings.log_raw_prompts else "[hidden]",
        tools=tools if settings.log_raw_prompts else tool_names(tools),
    )

    response = client.messages.create(
        model=model,
        max_tokens=300,
        messages=messages,
        tools=tools,
        system=system,
    )

    trace.record(
        "model.response",
        round=round_number,
        response=response if settings.log_raw_prompts else "[hidden]",
    )

    return response

def get_tool_uses(response: Any) -> list[ToolUseBlock]:
    return [
        block
        for block in response.content
        if isinstance(block, ToolUseBlock)
    ]


def final_answer_response(
    response: Any,
    trace: TraceLogger,
    used_tools: list[str],
) -> AskResponse:
    answer = text_from_blocks(response.content)
    trace.record("answer.ready", answer=answer, used_tools=used_tools)
    return AskResponse(
        answer=answer,
        used_tools=used_tools,
        run_id=trace.run_id,
        trace_file=str(trace.path),
    )

def max_rounds_response(
    settings: Settings,
    trace: TraceLogger,
    tool_uses: list[ToolUseBlock],
    used_tools: list[str],
) -> AskResponse:
    answer = (
        "Stopped before completing because the agent reached "
        f"MAX_TOOL_ROUNDS={settings.max_tool_rounds}."
    )
    trace.record(
        "agent.max_rounds_reached",
        requested_tools=[tool_use.name for tool_use in tool_uses],
        used_tools=used_tools,
    )
    return AskResponse(
        answer=answer,
        used_tools=used_tools,
        run_id=trace.run_id,
        trace_file=str(trace.path),
    )

async def execute_tool_uses(
    mcp: MCPToolBridge,
    tool_uses: list[ToolUseBlock],
    trace: TraceLogger,
    round_number: int,
    used_tools: list[str],
) -> list[dict[str, str]]:
    tool_results = []

    for tool_use in tool_uses:
        trace.record(
            "tool.selected",
            round=round_number,
            name=tool_use.name,
            input=tool_use.input,
            tool_use_id=tool_use.id,
        )

        tool_result = await mcp.call_tool(tool_use.name, tool_use.input)
        used_tools.append(tool_use.name)

        trace.record(
            "tool.result",
            round=round_number,
            name=tool_use.name,
            result=tool_result,
        )

        tool_results.append(
            {
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": json.dumps(tool_result),
            }
        )

    return tool_results

def append_assistant_response(messages, response) -> None:
    messages.append(
        {
            "role": "assistant",
            "content": cast(Any, response.content),
        }
    )

def append_tool_results(messages, tool_results) -> None:
    messages.append(
        {
            "role": "user",
            "content": cast(Any, tool_results),
        }
    )

def tool_names(tools: list[Any]) -> list[str]:
    return [
        str(tool.get("name", "unknown")) if isinstance(tool, dict) else "unknown"
        for tool in tools
    ]


def text_from_blocks(blocks: list[Any]) -> str:
    return "".join(
        block.text
        for block in blocks
        if isinstance(block, TextBlock)
    ).strip()
