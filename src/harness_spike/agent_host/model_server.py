from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast

from anthropic import Anthropic
from anthropic.types import MessageParam, TextBlock, ToolParam, ToolUseBlock
from fastmcp import Client
from pydantic import BaseModel, Field, ValidationError

from harness_spike.agent_host.trace_logger import TraceLogger
from harness_spike.config import get_settings


class AskRequest(BaseModel):
    question: str = Field(min_length=1)


class AskResponse(BaseModel):
    answer: str
    used_tools: list[str]
    run_id: str
    trace_file: str


# HTTPServer requires a handler class. The actual agent step is answer_question().
class Handler(BaseHTTPRequestHandler):
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
        data = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


async def answer_question(question: str) -> AskResponse:
    settings = get_settings()
    trace = TraceLogger()
    trace.record("request.received", question=question)

    async with Client(settings.mcp_weather_url) as mcp:
        available_tools = await mcp.list_tools()
        claude_tools: list[ToolParam] = [
            cast(
                ToolParam,
                {
                    "name": tool.name,
                    "description": tool.description or tool.name,
                    "input_schema": tool.inputSchema,
                },
            )
            for tool in available_tools
        ]
        trace.record(
            "mcp.tools.listed",
            mcp_url=settings.mcp_weather_url,
            tools=[
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                }
                for tool in available_tools
            ],
        )

        client = Anthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
            default_headers=settings.anthropic_custom_headers,
        )

        messages: list[MessageParam] = [{"role": "user", "content": question}]
        trace.record(
            "model.request.first",
            model=settings.claude_model,
            messages=messages,
            tools=claude_tools,
        )
        first_response = client.messages.create(
            model=settings.claude_model,
            max_tokens=300,
            messages=messages,
            tools=claude_tools,
        )
        trace.record("model.response.first", response=first_response)

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

        messages.append(
            {
                "role": "assistant",
                "content": first_response.content,
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": str(tool_result.data or tool_result.content),
                    }
                ],
            }
        )

        trace.record(
            "model.request.final",
            model=settings.claude_model,
            messages=messages,
            tools=claude_tools,
        )
        final_response = client.messages.create(
            model=settings.claude_model,
            max_tokens=300,
            messages=messages,
            tools=claude_tools,
        )
        trace.record("model.response.final", response=final_response)

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


if __name__ == "__main__":
    port = get_settings().model_port
    print(f"Model server: http://localhost:{port}/ask")
    HTTPServer(("localhost", port), Handler).serve_forever()
