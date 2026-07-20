"""Workflow for harmless questions that require no tools."""

from __future__ import annotations

from anthropic.types import TextBlock

from agent_host.budget import (
    BudgetExceeded,
    ExecutionBudget,
    ModelStopError,
)
from agent_host.config import Settings
from agent_host.model_call import build_model_client, call_model
from agent_host.responses import (
    budget_exceeded_response,
    model_stop_response,
    screened_answer_response,
)
from agent_host.schemas import AskResponse
from agent_host.trace_logger import TraceLogger
from mcp_servers.intent import IntentResult

_SYSTEM = (
    "Answer the user's harmless general question directly. Do not claim "
    "access to hospital data, local files, secrets, SQL execution, or "
    "external tools."
)


async def run_general_workflow(
    question: str,
    classification: IntentResult,
    settings: Settings,
    trace: TraceLogger,
    *,
    budget: ExecutionBudget,
) -> AskResponse:
    """Answer a harmless general question without exposing any MCP tools."""
    trace.record("general_question.started", intent=classification.model_dump())
    client = build_model_client(settings)

    try:
        response = call_model(
            client=client,
            model=settings.require_claude_model(),
            messages=[{"role": "user", "content": question}],
            tools=[],
            trace=trace,
            settings=settings,
            round_number=1,
            system=_SYSTEM,
            budget=budget,
        )
    except BudgetExceeded as exc:
        return budget_exceeded_response(trace, exc)
    except ModelStopError as exc:
        return model_stop_response(trace, exc, [])

    answer = "".join(
        block.text for block in response.content if isinstance(block, TextBlock)
    ).strip()
    return screened_answer_response(
        answer,
        trace,
        [],
        intent=classification.intent,
        intent_confidence=classification.confidence,
    )
