from __future__ import annotations

import asyncio
import json

from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast

from anthropic import Anthropic
from anthropic.types import MessageParam, TextBlock, ToolUseBlock
from pydantic import BaseModel, Field, ValidationError

from harness_spike.agent_host.mcp_bridge import MCPToolBridge
from harness_spike.agent_host.trace_logger import TraceLogger
from harness_spike.config import get_settings
from harness_spike.gates import policy_gate

class AskRequest(BaseModel):
    question: str = Field(min_length=1)

class AskResponse(BaseModel):
    answer: str
    used_tools: list[str]
    run_id: str
    trace_file: str
    allowed: bool = True
    policy_reason: str | None = None
    matched_term: str | None = None


async def ask(question: str) -> dict[str, Any]:
    """CLI-friendly wrapper around the same agent logic used by HTTP."""
    response = await answer_question(question)
    return response.model_dump()


class Handler(BaseHTTPRequestHandler):
    """HTTP wrapper: POST /ask in, JSON response out.

    This class is not the agent. It only translates HTTP requests into calls to
    answer_question().
    """

    def do_POST(self) -> None:
        if self.path != "/ask":
            self._json({"error": "POST to /ask"}, status=404)
            return

        try:
            body = self.rfile.read(int(self.headers.get("content-length", "0")))
            request = AskRequest.model_validate_json(body)
            response = asyncio.run(answer_question(request.question))
        except (json.JSONDecodeError, KeyError, ValidationError) as exc:
            self._json({"error": str(exc)}, status=400)
            return

        self._json(response.model_dump())

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        """Send a Python dictionary back to the HTTP client as JSON."""
        data = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


async def answer_question(question: str) -> AskResponse:
    """Runs a bounded agent loop.

    1. Discover tools from the MCP server.
    2. Ask Claude the user's question while showing it those tools.
    3. If Claude answers directly, return that answer.
    4. If Claude requests a tool, the host executes it through MCP.
    5. Send the tool result back to Claude and continue until answer or limit.
    """
    settings = get_settings()
    trace = TraceLogger(settings.trace_dir)
    trace.record(
        "request.received",
        question=question if settings.log_raw_prompts else "[hidden]",
    )

    gate_result = policy_gate(question)
    trace.record("policy_gate.checked", result=gate_result)
    if not gate_result["allowed"]:
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

    async with MCPToolBridge(settings.mcp_server_url) as mcp:
        claude_tools = await mcp.list_anthropic_tools()
        trace.record(
            "mcp.tools.listed",
            mcp_url=settings.mcp_server_url,
            tools=claude_tools if settings.log_raw_prompts else tool_names(claude_tools),
        )

        client = Anthropic(
            api_key=settings.require_anthropic_api_key(),
            base_url=settings.require_anthropic_base_url(),
            default_headers=settings.anthropic_custom_headers,
        )

        model = settings.require_claude_model()

        messages: list[MessageParam] = [{"role": "user", "content": question}]
        used_tools: list[str] = []
        model_call_number = 0
        tool_rounds_used = 0

        while True:
            model_call_number += 1
            trace.record(
                "model.request",
                round=model_call_number,
                model=model,
                messages=messages if settings.log_raw_prompts else "[hidden]",
                tools=claude_tools if settings.log_raw_prompts else tool_names(claude_tools),
            )

            response = client.messages.create(
                model=model,
                max_tokens=300,
                messages=messages,
                tools=claude_tools,
            )

            trace.record(
                "model.response",
                round=model_call_number,
                response=response if settings.log_raw_prompts else "[hidden]",
            )

            tool_uses = [
                block
                for block in response.content
                if isinstance(block, ToolUseBlock)
            ]

            if not tool_uses:
                answer = text_from_blocks(response.content)
                trace.record("answer.ready", answer=answer, used_tools=used_tools)
                return AskResponse(
                    answer=answer,
                    used_tools=used_tools,
                    run_id=trace.run_id,
                    trace_file=str(trace.path),
                )

            if tool_rounds_used >= settings.max_tool_rounds:
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

            messages.append(
                {
                    "role": "assistant",
                    "content": cast(Any, response.content),
                }
            )

            tool_results: list[dict[str, str]] = []
            tool_rounds_used += 1
            for tool_use in tool_uses:
                trace.record(
                    "tool.selected",
                    round=model_call_number,
                    name=tool_use.name,
                    input=tool_use.input,
                    tool_use_id=tool_use.id,
                )

                tool_result = await mcp.call_tool(tool_use.name, tool_use.input)
                used_tools.append(tool_use.name)
                trace.record(
                    "tool.result",
                    round=model_call_number,
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


if __name__ == "__main__":
    port = get_settings().model_port
    print(f"Model server: http://localhost:{port}/ask")
    HTTPServer(("localhost", port), Handler).serve_forever()
