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

class AskRequest(BaseModel):
    question: str = Field(min_length=1)

class AskResponse(BaseModel):
    answer: str
    used_tools: list[str]
    run_id: str
    trace_file: str

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
    """Run the smallest agent loop.

    1. Discover tools from the MCP server.
    2. Ask Claude the user's question while showing it those tools.
    3. If Claude answers directly, return that answer.
    4. If Claude requests a tool, the host executes it through MCP.
    5. Send the tool result back to Claude for the final answer.
    """
    settings = get_settings()
    trace = TraceLogger(settings.trace_dir)
    trace.record(
        "request.received",
        question=question if settings.log_raw_prompts else "[hidden]",
    )

    async with MCPToolBridge(settings.mcp_weather_url) as mcp:
        claude_tools = await mcp.list_anthropic_tools()
        trace.record(
            "mcp.tools.listed",
            mcp_url=settings.mcp_weather_url,
            tools=claude_tools if settings.log_raw_prompts else tool_names(claude_tools),
        )

        client = Anthropic(
            api_key=settings.require_anthropic_api_key(),
            base_url=settings.require_anthropic_base_url(),
            default_headers=settings.anthropic_custom_headers,
        )

        model = settings.require_claude_model()

        messages: list[MessageParam] = [{"role": "user", "content": question}]

        trace.record(
            "model.request.first",
            model=model,
            messages=messages if settings.log_raw_prompts else "[hidden]",
            tools=claude_tools if settings.log_raw_prompts else tool_names(claude_tools),
        )

        first_response = client.messages.create(
            model=model,
            max_tokens=300,
            messages=messages,
            tools=claude_tools,
        )

        trace.record(
            "model.response.first",
            response=first_response if settings.log_raw_prompts else "[hidden]",
        )

        tool_uses = [
            block
            for block in first_response.content
            if isinstance(block, ToolUseBlock)
        ]

        if not tool_uses:
            answer = "".join(
                block.text
                for block in first_response.content
                if isinstance(block, TextBlock)
            ).strip()
            trace.record("answer.ready", answer=answer, used_tools=[])
            return AskResponse(
                answer=answer,
                used_tools=[],
                run_id=trace.run_id,
                trace_file=str(trace.path),
            )

        tool_use = tool_uses[0]
        trace.record(
            "tool.selected",
            name=tool_use.name,
            input=tool_use.input,
            tool_use_id=tool_use.id,
        )

        tool_result = await mcp.call_tool(tool_use.name, tool_use.input)
        trace.record("tool.result", name=tool_use.name, result=tool_result)

        # Preserve Claude's tool request, then provide the host-executed result.
        messages.append(
            {
                "role": "assistant",
                "content": cast(Any, first_response.content),
            }
        )

        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": json.dumps(tool_result),
                    }
                ],
            }
        )

        trace.record(
            "model.request.final",
            model=model,
            messages=messages if settings.log_raw_prompts else "[hidden]",
            tools=claude_tools if settings.log_raw_prompts else tool_names(claude_tools),
        )
        final_response = client.messages.create(
            model=model,
            max_tokens=300,
            messages=messages,
            tools=claude_tools,
        )
        trace.record(
            "model.response.final",
            response=final_response if settings.log_raw_prompts else "[hidden]",
        )

        answer = "".join(
            block.text
            for block in final_response.content
            if isinstance(block, TextBlock)
        ).strip()
        trace.record("answer.ready", answer=answer, used_tools=[tool_use.name])
        return AskResponse(
            answer=answer,
            used_tools=[tool_use.name],
            run_id=trace.run_id,
            trace_file=str(trace.path),
        )


def tool_names(tools: list[Any]) -> list[str]:
    return [
        str(tool.get("name", "unknown")) if isinstance(tool, dict) else "unknown"
        for tool in tools
    ]


if __name__ == "__main__":
    port = get_settings().model_port
    print(f"Model server: http://localhost:{port}/ask")
    HTTPServer(("localhost", port), Handler).serve_forever()
