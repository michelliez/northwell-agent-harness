"""Routes for audit log retrieval and reporting."""

from __future__ import annotations

from fastapi import APIRouter, Request

from sql.audit_log import AuditLog

from ..models import AuditEventResponse, AuditRunResponse, AuditSummaryResponse

router = APIRouter()


@router.get("/audit/summary")
async def get_audit_summary(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    user_id: str | None = None,
) -> AuditSummaryResponse:
    """Retrieve summary of all audited queries.

    Args:
        start_date: Filter events after this ISO 8601 timestamp
        end_date: Filter events before this ISO 8601 timestamp
        user_id: Filter events for this user

    Returns:
        Summary of queries matching filters
    """
    audit_log: AuditLog = request.app.state.audit_log

    all_events = audit_log.get_all_events()

    # Filter by date and user if provided
    filtered: dict[str, dict] = {}
    for run_id, event in all_events:
        # Apply filters
        if start_date and event.timestamp < start_date:
            continue
        if end_date and event.timestamp > end_date:
            continue
        if user_id and event.user_id != user_id:
            continue

        # Initialize run entry
        if run_id not in filtered:
            filtered[run_id] = {
                "timestamp": event.timestamp,
                "user_id": event.user_id,
                "events": {},
            }

        # Record event decision
        filtered[run_id]["events"][event.event_type] = event.decision

    return AuditSummaryResponse(
        total_runs=len(filtered),
        runs=filtered,
        filters={
            "start_date": start_date,
            "end_date": end_date,
            "user_id": user_id,
        },
    )


@router.get("/audit/{run_id}")
async def get_run_audit(request: Request, run_id: str) -> AuditRunResponse:
    """Retrieve the audit trail for one run."""
    audit_log: AuditLog = request.app.state.audit_log
    events = audit_log.get_events(run_id)
    return AuditRunResponse(
        run_id=run_id,
        events=[
            AuditEventResponse(
                timestamp=e.timestamp,
                run_id=e.run_id,
                user_id=e.user_id,
                event_type=e.event_type,
                decision=e.decision,
                reason=e.reason,
                sql=e.sql,
                bytes_processed=e.bytes_processed,
            )
            for e in events
        ],
        summary=audit_log.summary_for_run(run_id),
    )
