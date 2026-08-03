"""Routes for asking questions and resuming with approvals."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from sql.audit_log import AuditEvent, AuditLog
from sql.cost_gate import ApprovalTokenError

from ..models import AskRequest, AskResponse, ResumeRequest, WorkflowEventResponse

router = APIRouter()


@router.post("/ask")
async def ask_question(request: Request, ask_req: AskRequest) -> AskResponse:
    """Submit a question to the SQL agent workflow.

    Returns workflow trace and results if successful.
    """
    workflow = request.app.state.workflow
    audit_log: AuditLog = request.app.state.audit_log

    # Generate run ID and set user
    run_id = str(uuid.uuid4())
    user_id = ask_req.user_id or "anonymous"

    try:
        # Call workflow directly (same as Streamlit does)
        result = workflow.invoke(
            {
                "question": ask_req.question,
                "user_id": user_id,
                "run_id": run_id,
            }
        )

        # Extract events and status from result
        events = result.get("events", [])
        final_status = result.get("status", "error")

        # Record all events in audit log
        for event_data in events:
            try:
                audit_event = AuditEvent(
                    timestamp=event_data.get("timestamp", ""),
                    run_id=run_id,
                    user_id=user_id,
                    event_type=event_data.get("event_type", ""),
                    decision=event_data.get("decision", "error"),
                    reason=event_data.get("reason"),
                    sql=event_data.get("sql"),
                    bytes_processed=event_data.get("bytes_processed"),
                    referenced_tables=event_data.get("referenced_tables"),
                )
                audit_log.record(audit_event)
            except ValueError:
                # Skip audit events with validation errors
                pass

        # Extract result from final event
        query_result = None
        if final_status == "complete":
            query_result = result.get("result")

        return AskResponse(
            run_id=run_id,
            events=[
                WorkflowEventResponse(
                    timestamp=e.get("timestamp", ""),
                    event_type=e.get("event_type", ""),
                    decision=e.get("decision", "error"),
                    reason=e.get("reason"),
                    metadata=e.get("metadata", {}),
                )
                for e in events
            ],
            status=final_status,
            result=query_result,
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Workflow execution failed: {str(e)}",
        )


@router.post("/resume")
async def resume_query(request: Request, resume_req: ResumeRequest) -> AskResponse:
    """Resume query execution after approval (for expensive queries).

    Validates approval token and executes with cost gate.
    """
    workflow = request.app.state.workflow
    audit_log: AuditLog = request.app.state.audit_log

    try:
        # Get the previous run's events to find user_id
        previous_events = audit_log.get_events(resume_req.run_id)
        user_id = "anonymous"
        if previous_events:
            user_id = previous_events[0].user_id or "anonymous"

        # Resume workflow with approval token
        result = workflow.invoke(
            {
                "run_id": resume_req.run_id,
                "approval_token": resume_req.approval_token,
                "user_id": user_id,
            },
            state_key="resumed",
        )

        # Extract events and status
        events = result.get("events", [])
        final_status = result.get("status", "error")

        # Record new events
        for event_data in events:
            try:
                audit_event = AuditEvent(
                    timestamp=event_data.get("timestamp", ""),
                    run_id=resume_req.run_id,
                    user_id=user_id,
                    event_type=event_data.get("event_type", ""),
                    decision=event_data.get("decision", "error"),
                    reason=event_data.get("reason"),
                    sql=event_data.get("sql"),
                    bytes_processed=event_data.get("bytes_processed"),
                    referenced_tables=event_data.get("referenced_tables"),
                )
                audit_log.record(audit_event)
            except ValueError:
                pass

        # Extract final result
        query_result = None
        if final_status == "complete":
            query_result = result.get("result")

        return AskResponse(
            run_id=resume_req.run_id,
            events=[
                WorkflowEventResponse(
                    timestamp=e.get("timestamp", ""),
                    event_type=e.get("event_type", ""),
                    decision=e.get("decision", "error"),
                    reason=e.get("reason"),
                    metadata=e.get("metadata", {}),
                )
                for e in events
            ],
            status=final_status,
            result=query_result,
        )

    except ApprovalTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid approval token: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Resume failed: {str(e)}")
