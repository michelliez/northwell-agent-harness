from __future__ import annotations

from pydantic import BaseModel


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
