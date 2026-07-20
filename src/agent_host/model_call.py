from __future__ import annotations

from typing import Any

from anthropic import Anthropic
from anthropic.types import MessageParam, ToolParam

from agent_host.budget import (
    ExecutionBudget,
    ModelStopError,
    budget_from_settings,
)
from agent_host.config import Settings
from agent_host.trace_logger import TraceLogger


def build_model_client(settings: Settings) -> Anthropic:
    return Anthropic(
        api_key=settings.require_anthropic_api_key(),
        base_url=settings.require_anthropic_base_url(),
        default_headers=settings.anthropic_custom_headers,
        timeout=getattr(settings, "model_call_timeout_seconds", 30.0),
    )


def call_model(
    client: Anthropic,
    model: str,
    messages: list[MessageParam],
    tools: list[ToolParam],
    trace: TraceLogger,
    settings: Settings,
    round_number: int,
    system: str,
    *,
    budget: ExecutionBudget | None = None,
) -> Any:
    budget = budget or budget_from_settings(settings)
    budget.reserve_model_call(messages)
    trace.record(
        "model.request",
        round=round_number,
        model=model,
        messages=messages if settings.log_raw_prompts else "[hidden]",
        tools=tools if settings.log_raw_prompts else tool_names(tools),
    )
    response = client.messages.create(
        model=model,
        max_tokens=getattr(settings, "model_max_tokens", budget.model_max_tokens),
        messages=messages,
        tools=tools,
        system=system,
    )
    stop_reason = getattr(response, "stop_reason", None)
    usage = getattr(response, "usage", None)
    trace.record(
        "model.response",
        round=round_number,
        response=response if settings.log_raw_prompts else "[hidden]",
        stop_reason=stop_reason,
        usage=usage if settings.log_raw_prompts else None,
    )
    if stop_reason in {"max_tokens", "refusal"}:
        raise ModelStopError(str(stop_reason))
    return response


def tool_names(tools: list[Any]) -> list[str]:
    return [
        str(tool.get("name", "unknown")) if isinstance(tool, dict) else "unknown" for tool in tools
    ]
