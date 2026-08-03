from __future__ import annotations

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """Readable source metadata paired with a stable internal chunk ID."""

    chunk_id: str
    label: str
    source_file: str
    heading_path: str | None = None
    category: str | None = None


class AskResponse(BaseModel):
    answer: str
    used_tools: list[str]
    run_id: str
    trace_file: str
    thread_id: str | None = None
    allowed: bool = True
    policy_reason: str | None = None
    matched_term: str | None = None
    intent: str | None = None
    intent_confidence: float | None = None
    disclosure_status: str | None = None
    interrupted: bool = False
    clarification_prompt: str | None = None
    generated_sql: str | None = None
    query_parameters: list[dict] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    citation_details: list[Citation] = Field(default_factory=list)
    execution_status: str | None = None
