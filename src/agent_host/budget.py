"""Per-request execution budget. All limits live here, not in AppConfig."""

from __future__ import annotations

import json
import os
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
    max_sql_repairs: int = 3
    max_input_bytes: int = 16_000
    max_tool_result_bytes: int = 32_000
    max_context_bytes: int = 128_000
    max_wall_seconds: float = 60.0
    model_call_timeout_seconds: float = 30.0
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
        self._check_bytes(result, self.max_tool_result_bytes, "max_tool_result_bytes")

    def reserve_round(self) -> None:
        self.check_wall()
        if self.rounds_used >= self.max_rounds:
            raise BudgetExceeded("max_rounds")
        self.rounds_used += 1

    def bound_retrieval_count(self, requested: int) -> int:
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


def budget_from_env() -> ExecutionBudget:
    """Build a budget from environment variable overrides, falling back to defaults."""

    def _int(name: str, default: int) -> int:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            v = int(raw)
        except ValueError as exc:
            raise RuntimeError(f"{name} must be an integer.") from exc
        if v < 0:
            raise RuntimeError(f"{name} must be 0 or greater.")
        return v

    def _float(name: str, default: float) -> float:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return float(raw)
        except ValueError as exc:
            raise RuntimeError(f"{name} must be numeric.") from exc

    return ExecutionBudget(
        max_rounds=_int("MAX_TOOL_ROUNDS", 3),
        max_model_calls=_int("MAX_MODEL_CALLS", 4),
        max_tool_calls=_int("MAX_TOOL_CALLS", 12),
        max_calls_per_tool=_int("MAX_CALLS_PER_TOOL", 6),
        max_candidate_schemas=_int("MAX_CANDIDATE_SCHEMAS", 5),
        max_retrieved_chunks=_int("MAX_RETRIEVED_CHUNKS", 5),
        max_sql_repairs=_int("MAX_SQL_REPAIRS", 3),
        max_input_bytes=_int("MAX_INPUT_BYTES", 16_000),
        max_tool_result_bytes=_int("MAX_TOOL_RESULT_BYTES", 32_000),
        max_context_bytes=_int("MAX_CONTEXT_BYTES", 128_000),
        max_wall_seconds=_float("MAX_WALL_SECONDS", 60.0),
        model_call_timeout_seconds=_float("MODEL_CALL_TIMEOUT_SECONDS", 30.0),
        model_max_tokens=_int("MODEL_MAX_TOKENS", 800),
        intent_max_tokens=_int("INTENT_MAX_TOKENS", 200),
        sql_generation_max_tokens=_int("SQL_GENERATION_MAX_TOKENS", 500),
    )


__all__ = [
    "BudgetExceeded",
    "ExecutionBudget",
    "ModelStopError",
    "budget_from_env",
]
