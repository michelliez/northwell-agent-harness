"""HTTP adapter for the LangGraph public ask/resume boundary."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from agent_host.schemas import AskResponse as GraphAskResponse

from ..models import (
    AgentResponse,
    AskRequest,
    CitationResponse,
    ClarificationRequest,
    ThreadClearResponse,
)

router = APIRouter()


@router.post("/ask", response_model=AgentResponse)
async def ask_question(request: Request, ask_req: AskRequest) -> AgentResponse:
    """Submit one question through the graph's supported public API."""
    try:
        response = await run_in_threadpool(
            request.app.state.ask_handler,
            ask_req.question,
            thread_id=ask_req.thread_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Agent workflow failed.") from exc
    return _to_api_response(response)


@router.post("/resume", response_model=AgentResponse)
async def resume_clarification(
    request: Request,
    resume_req: ClarificationRequest,
) -> AgentResponse:
    """Resume a thread paused for clarification, not cost approval."""
    try:
        response = await run_in_threadpool(
            request.app.state.resume_handler,
            resume_req.reply,
            thread_id=resume_req.thread_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Agent workflow resume failed.") from exc
    return _to_api_response(response)


@router.delete("/threads/{thread_id}", response_model=ThreadClearResponse)
async def clear_thread(request: Request, thread_id: str) -> ThreadClearResponse:
    """Forget bounded follow-up metadata and pending clarification state."""
    if not thread_id or len(thread_id) > 128:
        raise HTTPException(status_code=422, detail="Invalid thread ID.")
    try:
        await run_in_threadpool(request.app.state.clear_thread_handler, thread_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Thread cleanup failed.") from exc
    return ThreadClearResponse()


def _to_api_response(response: GraphAskResponse) -> AgentResponse:
    if response.thread_id is None:
        raise RuntimeError("graph response did not contain a thread_id")
    if response.interrupted:
        status = "interrupted"
    elif not response.allowed:
        status = "rejected"
    else:
        status = "complete"
    details_by_id = {item.chunk_id: item for item in response.citation_details}
    public_citations: list[CitationResponse] = []
    display_answer = response.answer
    for reference_number, chunk_id in enumerate(response.citations, start=1):
        display_answer = display_answer.replace(f"[{chunk_id}]", f"[{reference_number}]")
        detail = details_by_id.get(chunk_id)
        if detail is not None:
            public_citations.append(
                CitationResponse(
                    reference_number=reference_number,
                    label=detail.label,
                    source_file=detail.source_file,
                    heading_path=detail.heading_path,
                    category=detail.category,
                )
            )

    return AgentResponse(
        status=status,
        answer=display_answer,
        run_id=response.run_id,
        thread_id=response.thread_id,
        allowed=response.allowed,
        policy_reason=response.policy_reason,
        matched_term=response.matched_term,
        intent=response.intent,
        intent_confidence=response.intent_confidence,
        disclosure_status=response.disclosure_status,
        interrupted=response.interrupted,
        clarification_prompt=response.clarification_prompt,
        used_tools=response.used_tools,
        generated_sql=response.generated_sql,
        query_parameters=response.query_parameters,
        citation_details=public_citations,
        execution_status=response.execution_status,
    )
