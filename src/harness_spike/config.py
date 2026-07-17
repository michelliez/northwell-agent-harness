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
    max_tool_rounds: int = 3
    max_model_calls: int = 4
    max_tool_calls: int = 12
    max_calls_per_tool: int = 6
    max_candidate_schemas: int = 5
    max_input_bytes: int = 16_000
    max_tool_result_bytes: int = 32_000
    max_context_bytes: int = 128_000
    max_wall_seconds: float = 60.0
    mcp_call_timeout_seconds: float = 10.0
    model_call_timeout_seconds: float = 30.0
    model_max_tokens: int = 300
    intent_max_tokens: int = 200
    sql_generation_max_tokens: int = 500
    intent_min_confidence: float = 0.70
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

    int_defaults = {
        "max_tool_rounds": ("MAX_TOOL_ROUNDS", 3),
        "max_model_calls": ("MAX_MODEL_CALLS", 4),
        "max_tool_calls": ("MAX_TOOL_CALLS", 12),
        "max_calls_per_tool": ("MAX_CALLS_PER_TOOL", 6),
        "max_candidate_schemas": ("MAX_CANDIDATE_SCHEMAS", 5),
        "max_input_bytes": ("MAX_INPUT_BYTES", 16_000),
        "max_tool_result_bytes": ("MAX_TOOL_RESULT_BYTES", 32_000),
        "max_context_bytes": ("MAX_CONTEXT_BYTES", 128_000),
        "model_max_tokens": ("MODEL_MAX_TOKENS", 300),
        "intent_max_tokens": ("INTENT_MAX_TOKENS", 200),
        "sql_generation_max_tokens": ("SQL_GENERATION_MAX_TOKENS", 500),
    }
    parsed_ints: dict[str, int] = {}
    for field_name, (env_name, default) in int_defaults.items():
        try:
            parsed_ints[field_name] = _int_env(env_name, default)
        except ValueError as exc:
            raise RuntimeError(f"{env_name} must be an integer.") from exc
        if parsed_ints[field_name] < 0:
            raise RuntimeError(f"{env_name} must be 0 or greater.")

    try:
        max_wall_seconds = float(os.getenv("MAX_WALL_SECONDS", "60"))
        mcp_call_timeout_seconds = float(os.getenv("MCP_CALL_TIMEOUT_SECONDS", "10"))
        model_call_timeout_seconds = float(os.getenv("MODEL_CALL_TIMEOUT_SECONDS", "30"))
        intent_min_confidence = float(os.getenv("INTENT_MIN_CONFIDENCE", "0.70"))
    except ValueError as exc:
        raise RuntimeError(
            "MAX_WALL_SECONDS, MCP_CALL_TIMEOUT_SECONDS, MODEL_CALL_TIMEOUT_SECONDS, and "
            "INTENT_MIN_CONFIDENCE must be numeric."
        ) from exc
    if (
        max_wall_seconds <= 0
        or mcp_call_timeout_seconds <= 0
        or model_call_timeout_seconds <= 0
    ):
        raise RuntimeError("execution timeouts must be greater than 0.")
    if not 0 <= intent_min_confidence <= 1:
        raise RuntimeError("INTENT_MIN_CONFIDENCE must be between 0 and 1.")

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
        max_tool_rounds=parsed_ints["max_tool_rounds"],
        max_model_calls=parsed_ints["max_model_calls"],
        max_tool_calls=parsed_ints["max_tool_calls"],
        max_calls_per_tool=parsed_ints["max_calls_per_tool"],
        max_candidate_schemas=parsed_ints["max_candidate_schemas"],
        max_input_bytes=parsed_ints["max_input_bytes"],
        max_tool_result_bytes=parsed_ints["max_tool_result_bytes"],
        max_context_bytes=parsed_ints["max_context_bytes"],
        max_wall_seconds=max_wall_seconds,
        mcp_call_timeout_seconds=mcp_call_timeout_seconds,
        model_call_timeout_seconds=model_call_timeout_seconds,
        model_max_tokens=parsed_ints["model_max_tokens"],
        intent_max_tokens=parsed_ints["intent_max_tokens"],
        sql_generation_max_tokens=parsed_ints["sql_generation_max_tokens"],
        intent_min_confidence=intent_min_confidence,
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
