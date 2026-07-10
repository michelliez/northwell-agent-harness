from __future__ import annotations

from pydantic import BaseModel, Field


#Used by HTTP handler, Pydantic model for HTTP input, expects one question
class AskRequest(BaseModel):
    question: str = Field(min_length=1)

#Defines the shape of final response from agent
class AskResponse(BaseModel):
    answer: str
    used_tools: list[str]
    run_id: str
    trace_file: str
    allowed: bool = True
    policy_reason: str | None = None
    matched_term: str | None = None
