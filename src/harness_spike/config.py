from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError


class Settings(BaseModel):
    anthropic_api_key: str = Field(min_length=1)
    anthropic_base_url: str = Field(min_length=1)
    anthropic_custom_headers: dict[str, str] = Field(default_factory=dict)
    claude_model: str = Field(min_length=1)
    mcp_weather_url: str = Field(default="http://localhost:8000/mcp", min_length=1)
    model_port: int = Field(default=8080, ge=1, le=65535)


def get_settings() -> Settings:
    load_dotenv()

    try:
        return Settings(
            anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
            anthropic_base_url=os.environ["ANTHROPIC_BASE_URL"],
            anthropic_custom_headers=parse_custom_headers(
                os.getenv("ANTHROPIC_CUSTOM_HEADERS", "")
            ),
            claude_model=os.getenv("CLAUDE_MODEL", "claude-haiku-4-5"),
            mcp_weather_url=os.getenv("MCP_WEATHER_URL", "http://localhost:8000/mcp"),
            model_port=int(os.getenv("MODEL_PORT", "8080")),
        )
    except KeyError as exc:
        raise RuntimeError(f"Missing required environment variable: {exc.args[0]}") from exc
    except (ValueError, ValidationError) as exc:
        raise RuntimeError(f"Invalid environment configuration:\n{exc}") from exc


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
