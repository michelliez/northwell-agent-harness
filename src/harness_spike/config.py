from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    anthropic_base_url: str | None
    anthropic_custom_headers: dict[str, str] = field(default_factory=dict)
    claude_model: str | None = "claude-haiku-4-5-20251001"
    mcp_server_url: str = "http://localhost:8000/mcp"
    intent_mcp_url: str = "http://localhost:8002/mcp"
    sql_generation_mcp_url: str = "http://localhost:8003/mcp"
    sql_validation_mcp_url: str = "http://localhost:8004/mcp"
    rag_mcp_url: str = "http://localhost:8005/mcp"
    max_tool_rounds: int = 3
    trace_dir: str = "logs/runs"
    log_raw_prompts: bool = False


    def require_anthropic_api_key(self) -> str:
        if not self.anthropic_api_key:
            raise RuntimeError(
                "Set ANTHROPIC_API_KEY or AI_HUB_API_KEY in your environment "
                "or .env file."
            )
        return self.anthropic_api_key

    def require_anthropic_base_url(self) -> str:
        if not self.anthropic_base_url:
            raise RuntimeError(
                "Set ANTHROPIC_BASE_URL in your environment or .env file."
            )
        return self.anthropic_base_url

    def require_claude_model(self) -> str:
        if not self.claude_model:
            raise RuntimeError("Set CLAUDE_MODEL in your environment or .env file.")
        return self.claude_model


def get_settings() -> Settings:
    load_dotenv()

    try:
        max_tool_rounds = _int_env("MAX_TOOL_ROUNDS", 3)
    except ValueError as exc:
        raise RuntimeError("MAX_TOOL_ROUNDS must be an integer.") from exc
    if max_tool_rounds < 0:
        raise RuntimeError("MAX_TOOL_ROUNDS must be 0 or greater.")

    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or os.getenv("AI_HUB_API_KEY"),
        anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
        anthropic_custom_headers=parse_custom_headers(
            os.getenv("ANTHROPIC_CUSTOM_HEADERS", "")
        ),
        claude_model=os.getenv("CLAUDE_MODEL") or "claude-haiku-4-5-20251001",
        mcp_server_url=os.getenv("MCP_SERVER_URL") or "http://localhost:8000/mcp",
        intent_mcp_url=os.getenv("INTENT_MCP_URL") or "http://localhost:8002/mcp",
        sql_generation_mcp_url=(
            os.getenv("SQL_GENERATION_MCP_URL") or "http://localhost:8003/mcp"
        ),
        sql_validation_mcp_url=(
            os.getenv("SQL_VALIDATION_MCP_URL") or "http://localhost:8004/mcp"
        ),
        rag_mcp_url=os.getenv("RAG_MCP_URL") or "http://localhost:8005/mcp",
        max_tool_rounds=max_tool_rounds,
        trace_dir=os.getenv("TRACE_DIR", "logs/runs"),
        log_raw_prompts=_bool_env("LOG_RAW_PROMPTS", False),
    )


def parse_custom_headers(raw_headers: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in raw_headers.split(","):
        if not item.strip():
            continue
        name, _, value = item.partition(":")
        if not name or not value:
            raise RuntimeError(
                "Expected ANTHROPIC_CUSTOM_HEADERS like 'x-client-id: <client-id>'"
            )
        headers[name.strip()] = value.strip()
    return headers
