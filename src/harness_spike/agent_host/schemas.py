from __future__ import annotations

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1)

class AskResponse(BaseModel):
    answer: str
    used_tools: list[str]
    run_id: str
    trace_file: str
    allowed: bool = True
    policy_reason: str | None = None
    matched_term: str | None = None
    intent: str | None = None
    intent_confidence: float | None = None
