"""Capture Anthropic token usage in both observable run stores."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_host.trace_logger import TraceLogger
from sql.audit_log import AuditEvent, AuditLog


@dataclass(frozen=True)
class ModelUsage:
    """Normalized token counts from one completed Anthropic response."""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


def usage_from_response(response: Any) -> ModelUsage:
    """Extract usage from an Anthropic SDK response without SDK coupling."""
    usage = getattr(response, "usage", None)
    if usage is None:
        raise ValueError("Anthropic response did not include usage")

    def count(name: str) -> int:
        value = getattr(usage, name, 0) or 0
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"Anthropic usage.{name} must be a non-negative integer")
        return value

    return ModelUsage(
        input_tokens=count("input_tokens"),
        output_tokens=count("output_tokens"),
        cache_creation_input_tokens=count("cache_creation_input_tokens"),
        cache_read_input_tokens=count("cache_read_input_tokens"),
    )


def record_anthropic_usage(
    response: Any,
    *,
    trace: TraceLogger,
    artifact_path: str | Path,
    operation: str,
    model: str,
    user_id: str | None = None,
) -> ModelUsage:
    """Write one API call's usage to the trace and SQL-workflow audit log."""
    usage = usage_from_response(response)
    fields = {
        "provider": "anthropic",
        "operation": operation,
        "model": model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation_input_tokens": usage.cache_creation_input_tokens,
        "cache_read_input_tokens": usage.cache_read_input_tokens,
        "total_tokens": usage.total_tokens,
    }
    trace.record("model.usage", **fields)
    AuditLog(Path(artifact_path) / "audit_logs").record(
        AuditEvent(
            timestamp=datetime.now(UTC).isoformat(),
            run_id=trace.run_id,
            user_id=user_id,
            event_type="model_usage",
            decision="allowed",
            provider="anthropic",
            operation=operation,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
            total_tokens=usage.total_tokens,
        )
    )
    return usage


__all__ = ["ModelUsage", "record_anthropic_usage", "usage_from_response"]
