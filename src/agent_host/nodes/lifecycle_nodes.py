"""Lifecycle nodes: citations, final answer assembly, followup state."""

from __future__ import annotations

import re

from agent_host.config import get_config
from agent_host.state import AgentState
from agent_host.trace_logger import TraceLogger


def interpretation_and_citations_node(state: AgentState) -> dict:
    """Extract citations from the answer and append to accumulated citations."""
    answer = state.get("answer") or ""
    # Look for [chunk_id] references already in the answer
    cited_ids = re.findall(r"\[([a-f0-9]{10,})\]", answer)
    return {"citations": cited_ids}


def final_answer_node(state: AgentState) -> dict:
    """Finalize the answer and emit the trace record."""
    cfg = get_config()
    trace = _open_trace(state, cfg)

    answer = state.get("answer") or ""
    intent = state.get("intent")
    intent_confidence = state.get("intent_confidence")
    used_tools = _infer_used_tools(state)

    trace.record(
        "answer.ready",
        answer=answer,
        used_tools=used_tools,
        intent=intent,
        intent_confidence=intent_confidence,
    )

    return {}


def bounded_followup_node(state: AgentState) -> dict:
    """Prepare state for the next turn in a thread.

    Appends the current turn to history so the next turn can access it.
    Does not start a new answer or intent classification.
    """
    history = list(state.get("history") or [])
    question = state.get("question") or ""
    answer = state.get("answer") or ""

    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer})

    return {"history": history}


def _infer_used_tools(state: AgentState) -> list[str]:
    tools = []
    if state.get("retrieved_chunks"):
        tools.append("retrieve_documentation_context")
    if state.get("generated_sql"):
        tools.append("generate_sql")
    if state.get("validation_result"):
        tools.append("validate_sql")
    return tools


def _open_trace(state: AgentState, cfg) -> TraceLogger:
    run_id = state.get("run_id") or "unknown"
    trace_file = state.get("trace_file")
    if trace_file:
        trace_dir = str(trace_file).rsplit("/", 1)[0].rsplit("\\", 1)[0]
    else:
        trace_dir = str(cfg.trace_dir)
    return TraceLogger(
        trace_dir=trace_dir,
        run_id=run_id,
        content_mode=cfg.trace_content_mode,
    )
