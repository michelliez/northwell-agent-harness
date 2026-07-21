"""Small per-request execution budget used by host workflows."""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


class BudgetExceeded(RuntimeError):
    """Raised when a request would exceed a host-owned execution budget."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ModelStopError(RuntimeError):
    """Raised for explicit non-success model stop states."""

    def __init__(self, stop_reason: str) -> None:
        self.stop_reason = stop_reason
        super().__init__(stop_reason)


@dataclass
class ExecutionBudget:
    max_rounds: int = 3
    max_model_calls: int = 4
    max_tool_calls: int = 12
    max_calls_per_tool: int = 6
    max_candidate_schemas: int = 5
    max_retrieved_chunks: int = 5
    max_input_bytes: int = 16_000
    max_tool_result_bytes: int = 32_000
    max_context_bytes: int = 128_000
    max_wall_seconds: float = 60.0
    mcp_call_timeout_seconds: float = 10.0
    model_max_tokens: int = 800
    intent_max_tokens: int = 200
    sql_generation_max_tokens: int = 500
    started_at: float = field(default_factory=time.monotonic)
    rounds_used: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    tool_calls_by_name: Counter[str] = field(default_factory=Counter)

    def check_wall(self) -> None:
        if time.monotonic() - self.started_at > self.max_wall_seconds:
            raise BudgetExceeded("max_wall_seconds")

    def reserve_model_call(self, messages: Any) -> None:
        self.check_wall()
        self._check_bytes(messages, self.max_input_bytes, "max_input_bytes")
        if self.model_calls >= self.max_model_calls:
            raise BudgetExceeded("max_model_calls")
        self.model_calls += 1

    def reserve_tool_call(self, name: str, arguments: Any) -> None:
        self.check_wall()
        self._check_bytes(arguments, self.max_input_bytes, "max_input_bytes")
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExceeded("max_tool_calls")
        if self.tool_calls_by_name[name] >= self.max_calls_per_tool:
            raise BudgetExceeded(f"max_calls_per_tool:{name}")
        self.tool_calls += 1
        self.tool_calls_by_name[name] += 1

    def accept_tool_result(self, result: Any) -> None:
        self.check_wall()
        self._check_bytes(
            result,
            self.max_tool_result_bytes,
            "max_tool_result_bytes",
        )

    def reserve_round(self) -> None:
        self.check_wall()
        if self.rounds_used >= self.max_rounds:
            raise BudgetExceeded("max_rounds")
        self.rounds_used += 1

    def bound_retrieval_count(self, requested: int) -> int:
        """Apply the host-owned retrieval breadth limit."""
        self.check_wall()
        if requested < 1:
            raise ValueError("requested retrieval count must be at least 1")
        if self.max_retrieved_chunks < 1:
            raise BudgetExceeded("max_retrieved_chunks")
        return min(requested, self.max_retrieved_chunks)

    def check_context(self, messages: Any) -> None:
        self.check_wall()
        self._check_bytes(messages, self.max_context_bytes, "max_context_bytes")

    @staticmethod
    def _check_bytes(value: Any, limit: int, reason: str) -> None:
        try:
            encoded = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise BudgetExceeded(f"{reason}:not_serializable") from exc
        if len(encoded) > limit:
            raise BudgetExceeded(reason)


def budget_from_settings(settings: Any) -> ExecutionBudget:
    """Build a budget while keeping lightweight test settings compatible."""

    def get(name: str, default: Any) -> Any:
        return getattr(settings, name, default)

    return ExecutionBudget(
        max_rounds=get("max_tool_rounds", 3),
        max_model_calls=get("max_model_calls", 4),
        max_tool_calls=get("max_tool_calls", 12),
        max_calls_per_tool=get("max_calls_per_tool", 6),
        max_candidate_schemas=get("max_candidate_schemas", 5),
        max_retrieved_chunks=get("max_retrieved_chunks", 5),
        max_input_bytes=get("max_input_bytes", 16_000),
        max_tool_result_bytes=get("max_tool_result_bytes", 32_000),
        max_context_bytes=get("max_context_bytes", 128_000),
        max_wall_seconds=get("max_wall_seconds", 60.0),
        mcp_call_timeout_seconds=get("mcp_call_timeout_seconds", 10.0),
        model_max_tokens=get("model_max_tokens", 800),
        intent_max_tokens=get("intent_max_tokens", 200),
        sql_generation_max_tokens=get("sql_generation_max_tokens", 500),
    )


__all__ = [
    "BudgetExceeded",
    "ExecutionBudget",
    "ModelStopError",
    "budget_from_settings",
]
