"""Chainlit frontend for the FastAPI/LangGraph public contract.

The existing Streamlit frontend remains available in ``ui.app``. Chainlit is
only a presentation layer: policy, retrieval authority, clarification state,
and SQL validation remain owned by FastAPI and LangGraph.
"""

from __future__ import annotations

import html
import os
from contextlib import suppress
from typing import Any

import chainlit as cl

from ui.api_client import AgentAPIError, AsyncAgentAPIClient

API_BASE_URL = os.getenv("AGENT_API_URL", "http://localhost:8000")
api = AsyncAgentAPIClient(base_url=API_BASE_URL)

# Presentation-only labels for backend graph nodes; unknown nodes fall back to
# a prettified node name so new pipeline stages appear without a UI change.
_STEP_LABELS = {
    "input_policy": "Screening request",
    "contextualize_followup": "Resolving follow-up context",
    "classify_intent": "Classifying intent",
    "intent_refusal": "Preparing refusal",
    "general_answer": "Drafting answer",
    "retrieval_permission": "Checking retrieval permission",
    "exploration": "Exploring documentation",
    "retrieve_context": "Retrieving documentation",
    "context_gate": "Assembling schema evidence",
    "documentation_answer": "Drafting answer",
    "query_plan": "Proposing query plan",
    "plan_safety": "Authorizing query plan",
    "write_sql": "Compiling SQL",
    "validate_sql": "Validating SQL",
    "execution_not_configured": "Preparing SQL draft",
    "classify_output_safety": "Screening output safety",
    "result_safety": "Checking result safety",
    "interpretation_and_citations": "Attaching citations",
    "final_answer": "Finalizing answer",
}
_SILENT_NODES = {"bounded_followup"}


@cl.set_starters
async def starters(
    user: cl.User | None = None,
    language: str | None = None,
) -> list[cl.Starter]:
    """Offer useful first turns on Chainlit's empty-chat landing screen."""
    del user, language
    return [
        cl.Starter(
            label="Explore a Clarity table",
            message="What is the A0H_MAP table?",
        ),
        cl.Starter(
            label="Find documented columns",
            message="Which columns describe account configuration?",
        ),
        cl.Starter(
            label="Locate a metric",
            message="Which tables contain a VISITS column?",
        ),
        cl.Starter(
            label="Draft validated SQL",
            message="Write SQL to count all rows in the A0H_MAP table using COUNT(*).",
        ),
    ]


@cl.on_chat_start
async def start_chat() -> None:
    """Initialize bounded browser-session control state."""
    cl.user_session.set("thread_id", None)
    cl.user_session.set("awaiting_clarification", False)


@cl.on_message
async def handle_message(message: cl.Message) -> None:
    """Send one user turn through the supported FastAPI boundary."""
    thread_id = cl.user_session.get("thread_id")
    awaiting_clarification = bool(cl.user_session.get("awaiting_clarification"))

    if awaiting_clarification:
        try:
            if not isinstance(thread_id, str) or not thread_id:
                _clear_control_state()
                raise AgentAPIError(
                    "The clarification session expired. Please submit the full question again."
                )
            response = await api.resume(message.content, thread_id)
        except AgentAPIError as exc:
            await cl.Message(
                author="Clarity Assistant",
                content=f"**Request unavailable**\n\n{exc}",
            ).send()
            return
        _apply_response_state(response)
        await cl.Message(
            author="Clarity Assistant",
            content=format_response(response),
        ).send()
        return

    # Stream the run: one message shows each pipeline step as it completes,
    # then is rewritten in place with the final answer.
    status = cl.Message(author="Clarity Assistant", content="- working…")
    await status.send()
    response = None
    completed: list[tuple[str, str]] = []
    try:
        async for event in api.ask_stream(
            message.content,
            thread_id if isinstance(thread_id, str) else None,
        ):
            kind = event.get("type")
            if kind == "step":
                node = str(event.get("node") or "")
                step_status = str(event.get("status") or "ok")
                if node in _SILENT_NODES:
                    continue
                label = _STEP_LABELS.get(node, node.replace("_", " ").capitalize())
                if not any(lbl == label for lbl, _ in completed):
                    completed.append((label, step_status))
                marks = [f"- {lbl} {'⚠' if s == 'degraded' else '✓'}" for lbl, s in completed]
                status.content = "\n".join(marks + ["- working…"])
                await status.update()
            elif kind == "response":
                response = event.get("data")
            elif kind == "error":
                raise AgentAPIError(_plain(event.get("detail") or "The API request failed."))
    except AgentAPIError as exc:
        status.content = f"**Request unavailable**\n\n{exc}"
        await status.update()
        return

    if not isinstance(response, dict):
        status.content = "**Request unavailable**\n\nThe stream ended without a final response."
        await status.update()
        return

    _apply_response_state(response)
    status.content = format_response(response)
    await status.update()


@cl.on_chat_end
async def end_chat() -> None:
    """Clear the backend's bounded thread state when the chat session ends."""
    thread_id = cl.user_session.get("thread_id")
    if isinstance(thread_id, str) and thread_id:
        # Browser disconnects must not turn backend cleanup into a user error.
        with suppress(AgentAPIError):
            await api.clear_thread(thread_id)
    _clear_control_state()


def format_response(response: dict[str, Any]) -> str:
    """Render the public API response as safe, readable Markdown."""
    status = response.get("status")
    sections: list[str] = []

    if status == "rejected":
        reason = response.get("policy_reason") or "This request is outside the allowed scope."
        return f"**Request blocked**\n\n{_plain(reason)}"

    answer = str(response.get("answer") or "").strip()
    if answer:
        sections.append(answer)

    if response.get("interrupted"):
        prompt = response.get("clarification_prompt") or "Please clarify your request."
        sections.append(f"**One detail needed**\n\n{_plain(prompt)}")

    generated_sql = str(response.get("generated_sql") or "").strip()
    if generated_sql:
        sections.append(f"### Validated SQL draft\n```sql\n{generated_sql}\n```")
        if response.get("execution_status") == "not_configured":
            sections.append(
                "_This draft passed static validation. Database execution is not configured._"
            )

    parameters = response.get("query_parameters") or []
    if parameters:
        rendered = "\n".join(
            f"- `{_plain(item.get('name', 'parameter'))}`: `{_plain(item.get('value', ''))}`"
            for item in parameters
            if isinstance(item, dict)
        )
        if rendered:
            sections.append(f"### Named parameters\n{rendered}")

    citations = response.get("citation_details") or []
    if citations:
        sources = []
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            number = citation.get("reference_number", "")
            label = _plain(citation.get("label") or "Documentation")
            source = _plain(citation.get("source_file") or "")
            sources.append(f"- **[{number}] {label}** — `{source}`")
        if sources:
            sections.append("### Sources\n" + "\n".join(sources))

    return "\n\n".join(sections) or "The request completed without a displayable answer."


def _apply_response_state(response: dict[str, Any]) -> None:
    if response.get("status") == "rejected":
        _clear_control_state()
        return

    cl.user_session.set("thread_id", response.get("thread_id"))
    cl.user_session.set("awaiting_clarification", bool(response.get("interrupted")))


def _clear_control_state() -> None:
    cl.user_session.set("thread_id", None)
    cl.user_session.set("awaiting_clarification", False)


def _plain(value: Any) -> str:
    """Escape backend metadata before embedding it in Markdown/HTML-capable UI."""
    return html.escape(str(value), quote=True)
