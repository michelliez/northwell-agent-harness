from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from agent_host.trace_logger import TraceContentMode


@dataclass(frozen=True)
class AppConfig:
    """External/runtime configuration only. All limits live in ExecutionBudget."""

    anthropic_api_key: str | None
    anthropic_base_url: str | None
    anthropic_custom_headers: dict[str, str] = field(default_factory=dict)
    model: str = "claude-sonnet-4-6"
    index_path: Path = field(default_factory=lambda: Path(".local/rag/index.sqlite"))
    artifact_path: Path = field(default_factory=lambda: Path(".local"))
    intent_min_confidence: float = 0.70
    trace_content_mode: TraceContentMode = "metadata"

    @property
    def trace_dir(self) -> Path:
        return self.artifact_path / "traces"

    def require_api_key(self) -> str:
        if not self.anthropic_api_key:
            raise RuntimeError(
                "Set ANTHROPIC_API_KEY or AI_HUB_API_KEY in your environment or .env file."
            )
        return self.anthropic_api_key

    def require_base_url(self) -> str:
        if not self.anthropic_base_url:
            raise RuntimeError("Set ANTHROPIC_BASE_URL in your environment or .env file.")
        return self.anthropic_base_url

    def require_model(self) -> str:
        return self.model


def get_config() -> AppConfig:
    load_dotenv()

    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("AI_HUB_API_KEY") or None
    base_url = os.getenv("ANTHROPIC_BASE_URL") or None
    model = os.getenv("CLAUDE_MODEL") or "claude-sonnet-4-6"

    raw_path = os.getenv("RAG_DB_PATH", "").strip()
    index_path = Path(raw_path) if raw_path else Path(".local/rag/index.sqlite")

    raw_artifact = os.getenv("ARTIFACT_PATH", "").strip()
    artifact_path = Path(raw_artifact) if raw_artifact else Path(".local")

    try:
        intent_min_confidence = float(os.getenv("INTENT_MIN_CONFIDENCE", "0.70"))
    except ValueError as exc:
        raise RuntimeError("INTENT_MIN_CONFIDENCE must be numeric.") from exc
    if not 0 <= intent_min_confidence <= 1:
        raise RuntimeError("INTENT_MIN_CONFIDENCE must be between 0 and 1.")

    raw_trace_mode = os.getenv("TRACE_CONTENT_MODE", "metadata").strip().lower()
    if raw_trace_mode not in {"metadata", "debug"}:
        raise RuntimeError("TRACE_CONTENT_MODE must be 'metadata' or 'debug'.")
    trace_content_mode: TraceContentMode = raw_trace_mode  # type: ignore[assignment]

    return AppConfig(
        anthropic_api_key=api_key,
        anthropic_base_url=base_url,
        anthropic_custom_headers=_parse_custom_headers(os.getenv("ANTHROPIC_CUSTOM_HEADERS", "")),
        model=model,
        index_path=index_path,
        artifact_path=artifact_path,
        intent_min_confidence=intent_min_confidence,
        trace_content_mode=trace_content_mode,
    )


def _parse_custom_headers(raw_headers: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in raw_headers.split(","):
        if not item.strip():
            continue
        name, _, value = item.partition(":")
        if not name or not value:
            raise RuntimeError("Expected ANTHROPIC_CUSTOM_HEADERS like 'x-client-id: <client-id>'")
        headers[name.strip()] = value.strip()
    return headers
