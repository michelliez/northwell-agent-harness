"""Versioned HTTP contracts for the agent API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AskRequest(BaseModel):
    """Start a new graph turn or continue a non-interrupted thread."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = Field(default=None, min_length=1, max_length=128)


class ClarificationRequest(BaseModel):
    """Resume a graph that paused through LangGraph ``interrupt()``."""

    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1, max_length=2000)
    thread_id: str = Field(min_length=1, max_length=128)


class CitationResponse(BaseModel):
    """User-readable citation without internal retrieval identifiers."""

    reference_number: int = Field(ge=1)
    label: str
    source_file: str
    heading_path: str | None = None
    category: str | None = None


class AgentResponse(BaseModel):
    """Stable client-facing projection of ``agent_host.schemas.AskResponse``."""

    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"] = "v1"
    status: Literal["complete", "interrupted", "rejected"]
    answer: str
    run_id: str
    thread_id: str
    allowed: bool
    policy_reason: str | None = None
    matched_term: str | None = None
    intent: str | None = None
    intent_confidence: float | None = None
    disclosure_status: str | None = None
    interrupted: bool = False
    clarification_prompt: str | None = None
    used_tools: list[str] = Field(default_factory=list)
    generated_sql: str | None = None
    query_parameters: list[dict[str, Any]] = Field(default_factory=list)
    citation_details: list[CitationResponse] = Field(default_factory=list)
    execution_status: str | None = None


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
    provider: str | None = None
    operation: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    total_tokens: int | None = None


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

    status: Literal["ok"]
    version: str = "0.1.0"


class ThreadClearResponse(BaseModel):
    """Idempotent response for clearing server-side thread state."""

    api_version: Literal["v1"] = "v1"
    cleared: Literal[True] = True
