"""Request and response models for the FastAPI backend."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """Request to ask a question."""

    question: str = Field(min_length=1, max_length=2000)
    user_id: str | None = None


class WorkflowEventResponse(BaseModel):
    """One event in the workflow trace."""

    timestamp: str
    event_type: str
    decision: Literal["allowed", "rejected", "error"]
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AskResponse(BaseModel):
    """Response from asking a question."""

    run_id: str
    events: list[WorkflowEventResponse]
    status: Literal["complete", "rejected", "error"]
    result: list[dict] | None = None


class ResumeRequest(BaseModel):
    """Request to resume with approval token."""

    run_id: str = Field(min_length=1)
    approval_token: str = Field(min_length=1)


class AuditEventResponse(BaseModel):
    """Single audit event for retrieval."""

    timestamp: str
    run_id: str
    user_id: str | None
    event_type: str
    decision: str
    reason: str | None = None
    sql: str | None = None
    bytes_processed: int | None = None


class AuditRunResponse(BaseModel):
    """Audit trail for a single run."""

    run_id: str
    events: list[AuditEventResponse]
    summary: dict[str, dict[str, int]]


class AuditSummaryResponse(BaseModel):
    """Summary of multiple audited runs."""

    total_runs: int
    runs: dict[str, Any]
    filters: dict[str, str | None]


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str = "0.1.0"
