from __future__ import annotations

from typing import Any, cast

from anthropic.types import ToolParam
from fastmcp import Client

from harness_spike.agent_host.trace_logger import jsonable


class MCPToolBridge:
    """Small adapter between the MCP server and Anthropic's tool API.

    MCP and Anthropic describe tools with slightly different Python objects.
    This class keeps that translation out of the agent host.
    """

    def __init__(self, server_source: str, *, auth_token: str | None = None) -> None:
        # FastMCP sends this as a bearer token for HTTP transports.  A missing
        # token is intentionally not replaced with a development default; an
        # authenticated MCP server should reject the request.
        self.client = Client(server_source, auth=auth_token)

    async def __aenter__(self) -> MCPToolBridge:
        await self.client.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.client.__aexit__(exc_type, exc, tb)

    async def list_anthropic_tools(self) -> list[ToolParam]:
        """Ask the MCP server for tools and format them for Claude."""
        mcp_tools = await self.client.list_tools()
        anthropic_tools: list[ToolParam] = []

        for tool in mcp_tools:
            name = str(getattr(tool, "name", ""))
            description = getattr(tool, "description", None) or name
            input_schema = getattr(tool, "inputSchema", None) or {
                "type": "object",
                "properties": {},
            }
            anthropic_tools.append(
                cast(
                    ToolParam,
                    {
                        "name": name,
                        "description": description,
                        "input_schema": jsonable(input_schema),
                    },
                )
            )

        return anthropic_tools

    async def call_tool(self, name: str, arguments: Any) -> Any:
        """Call an MCP tool selected by Claude and return plain JSON data."""
        if not isinstance(arguments, dict):
            arguments = {}

        result = await self.client.call_tool(name, arguments)

        data = getattr(result, "data", None)
        if data is not None:
            return jsonable(data)

        content = getattr(result, "content", None)
        if content is not None:
            return jsonable(content)

        return jsonable(result)
