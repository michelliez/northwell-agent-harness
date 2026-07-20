"""Shared fail-closed response and content-screen handling."""

from __future__ import annotations

from typing import Any

from anthropic.types import TextBlock, ToolUseBlock

from agent_host.budget import BudgetExceeded, ModelStopError
from agent_host.config import Settings
from agent_host.schemas import AskResponse
from agent_host.tool_registry import ToolContractError
from agent_host.trace_logger import TraceLogger
from policy.screen import (
    ContentScreenBlocked,
    ContentScreenResult,
    ContentSurface,
    screen_content,
)


def record_content_screen(
    trace: TraceLogger,
    result: ContentScreenResult,
    *,
    location: str,
    used_tools: list[str] | None = None,
) -> None:
    """Record a screen decision without persisting screened content."""
    trace.record(
        "content.screened",
        surface=result.surface.value,
        location=location,
        allowed=result.allowed,
        reason=result.reason,
        matched_term=result.matched_term,
        used_tools=used_tools or [],
    )
    if not result.allowed:
        trace.record(
            "content.blocked",
            surface=result.surface.value,
            location=location,
            reason=result.reason,
            matched_term=result.matched_term,
        )


def blocked_response_if_needed(question: str, trace: TraceLogger) -> AskResponse | None:
    screen_result = screen_content(question, ContentSurface.USER_INPUT)
    gate_result = screen_result.as_policy_result()
    trace.record(
        "policy_gate.checked",
        result=gate_result,
        surface=screen_result.surface.value,
    )
    if screen_result.allowed:
        return None

    trace.record(
        "request.blocked",
        reason=gate_result["reason"],
        matched_term=gate_result["matched_term"],
    )
    return AskResponse(
        answer=(
            "I can't help with that request because it is blocked by the "
            f"policy screen: {gate_result['reason']}."
        ),
        used_tools=[],
        run_id=trace.run_id,
        trace_file=str(trace.path),
        allowed=False,
        policy_reason=gate_result["reason"],
        matched_term=gate_result["matched_term"],
    )


def final_answer_response(response: Any, trace: TraceLogger, used_tools: list[str]) -> AskResponse:
    answer = "".join(
        block.text for block in response.content if isinstance(block, TextBlock)
    ).strip()
    return screened_answer_response(answer, trace, used_tools)


def screened_answer_response(
    answer: str,
    trace: TraceLogger,
    used_tools: list[str],
    *,
    intent: str | None = None,
    intent_confidence: float | None = None,
) -> AskResponse:
    screen_result = screen_content(answer, ContentSurface.FINAL_ANSWER)
    record_content_screen(
        trace,
        screen_result,
        location="final_answer",
        used_tools=used_tools,
    )
    if not screen_result.allowed:
        return content_blocked_response(
            trace,
            ContentScreenBlocked(screen_result, "final_answer", used_tools),
        )

    trace.record("answer.ready", answer=answer, used_tools=used_tools)
    return AskResponse(
        answer=answer,
        used_tools=used_tools,
        run_id=trace.run_id,
        trace_file=str(trace.path),
        intent=intent,
        intent_confidence=intent_confidence,
    )


def unauthorized_tool_response(
    trace: TraceLogger, used_tools: list[str], tools: list[str]
) -> AskResponse:
    answer = "I stopped because the requested tool is outside the approved intent scope."
    trace.record("tool.scope.blocked", blocked_tools=tools)
    return screened_answer_response(answer, trace, used_tools)


def content_blocked_response(
    trace: TraceLogger,
    failure: ContentScreenBlocked,
) -> AskResponse:
    result = failure.result
    reason = result.reason or "content failed the deterministic screen"
    trace.record(
        "request.blocked",
        reason=reason,
        matched_term=result.matched_term,
        surface=result.surface.value,
        location=failure.location,
    )
    trace.record(
        "answer.blocked",
        surface=result.surface.value,
        location=failure.location,
        used_tools=failure.used_tools,
    )
    return AskResponse(
        answer=(
            "I stopped because content at the "
            f"{failure.location} boundary failed the policy screen."
        ),
        used_tools=failure.used_tools,
        run_id=trace.run_id,
        trace_file=str(trace.path),
        allowed=False,
        policy_reason=f"content_screen:{result.reason or 'blocked'}",
        matched_term=result.matched_term,
    )


def tool_contract_error_response(
    trace: TraceLogger,
    failure: ToolContractError,
    used_tools: list[str] | None = None,
) -> AskResponse:
    tools = used_tools or []
    trace.record(
        "mcp.contract.failed",
        tool=failure.tool,
        reason=failure.reason,
        used_tools=tools,
    )
    return AskResponse(
        answer="I stopped because an MCP tool did not match the host contract.",
        used_tools=tools,
        run_id=trace.run_id,
        trace_file=str(trace.path),
        allowed=False,
        policy_reason="mcp_contract_mismatch",
    )


def budget_exceeded_response(
    trace: TraceLogger,
    failure: BudgetExceeded,
    used_tools: list[str] | None = None,
) -> AskResponse:
    tools = used_tools or []
    trace.record("budget.exceeded", reason=failure.reason, used_tools=tools)
    return AskResponse(
        answer="I stopped because the request exceeded the execution budget.",
        used_tools=tools,
        run_id=trace.run_id,
        trace_file=str(trace.path),
        allowed=False,
        policy_reason=f"execution_budget_exceeded:{failure.reason}",
    )


def model_stop_response(
    trace: TraceLogger,
    failure: ModelStopError,
    used_tools: list[str],
) -> AskResponse:
    event = "model.refused" if failure.stop_reason == "refusal" else "model.truncated"
    trace.record(event, stop_reason=failure.stop_reason, used_tools=used_tools)
    if failure.stop_reason == "refusal":
        answer = "I can't help with that request because the model refused it."
        reason = "model_refused"
    else:
        answer = "I stopped because the model response reached its output limit."
        reason = "model_max_tokens"
    return AskResponse(
        answer=answer,
        used_tools=used_tools,
        run_id=trace.run_id,
        trace_file=str(trace.path),
        allowed=False,
        policy_reason=reason,
    )


def max_rounds_response(
    settings: Settings,
    trace: TraceLogger,
    tool_uses: list[ToolUseBlock],
    used_tools: list[str],
) -> AskResponse:
    answer = (
        "Stopped before completing because the agent reached "
        f"MAX_TOOL_ROUNDS={settings.max_tool_rounds}."
    )
    trace.record(
        "agent.max_rounds_reached",
        requested_tools=[tool_use.name for tool_use in tool_uses],
        used_tools=used_tools,
    )
    return screened_answer_response(answer, trace, used_tools)


def unsupported_workflow_response(
    classification_intent: str,
    trace: TraceLogger,
) -> AskResponse:
    """Fail closed when an allowed classifier label has no registered workflow."""
    trace.record("workflow.unavailable", intent=classification_intent)
    return AskResponse(
        answer="I stopped because that request does not have an available workflow.",
        used_tools=[],
        run_id=trace.run_id,
        trace_file=str(trace.path),
        allowed=False,
        policy_reason="workflow_unavailable",
        intent=classification_intent,
    )
