from __future__ import annotations

from typing import Any

from harness_spike.agent_host.agent_loop import (
    blocked_response_if_needed,
    discover_tools,
    run_agent_loop,
)
from harness_spike.agent_host.mcp_bridge import MCPToolBridge
from harness_spike.agent_host.model_client import build_model_client
from harness_spike.agent_host.schemas import AskResponse
from harness_spike.agent_host.trace_logger import TraceLogger
from harness_spike.config import get_settings


async def ask(question: str) -> dict[str, Any]:
    """CLI-friendly wrapper around the same agent logic used by HTTP."""
    response = await answer_question(question)
    return response.model_dump()


async def answer_question(question: str) -> AskResponse:
    """Run the agent host for one question."""
    settings = get_settings()
    trace = TraceLogger(settings.trace_dir)
    trace.record(
        "request.received",
        question=question if settings.log_raw_prompts else "[hidden]",
    )

    blocked_response = blocked_response_if_needed(question, trace)
    if blocked_response is not None:
        return blocked_response

    async with MCPToolBridge(settings.mcp_server_url) as mcp:
        tools = await discover_tools(mcp, settings, trace)
        client = build_model_client(settings)
        model = settings.require_claude_model()

        return await run_agent_loop(
            question=question,
            client=client,
            model=model,
            tools=tools,
            mcp=mcp,
            settings=settings,
            trace=trace,
        )
