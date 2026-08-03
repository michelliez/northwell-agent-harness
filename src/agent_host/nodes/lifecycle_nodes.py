"""Lifecycle nodes: citations, final answer assembly, followup state."""

from __future__ import annotations

import re

from agent_host.config import get_config
from agent_host.state import AgentState
from agent_host.trace_logger import TraceLogger


def interpretation_and_citations_node(state: AgentState) -> dict:
    """Extract citations from the answer and append to accumulated citations."""
    answer = state.get("answer") or ""
    available_ids = {
        str(chunk.get("chunk_id"))
        for chunk in state.get("retrieved_chunks", [])
        if chunk.get("chunk_id")
    }
    existing = set(state.get("citations") or [])
    cited_ids = [
        candidate
        for candidate in re.findall(r"\[([^\[\]]+)\]", answer)
        if candidate in available_ids and candidate not in existing
    ]
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
    """End the turn without copying answer content into conversation history."""
    return {}


def _infer_used_tools(state: AgentState) -> list[str]:
    tools = []
    if state.get("retrieved_chunks"):
        tools.append("retrieve_documentation_context")
    raw_compiled = state.get("compiled_query") or {}
    if raw_compiled.get("source") == "deterministic":
        tools.append("compile_sql")
    elif raw_compiled.get("source") == "claude_repair":
        tools.append("fix_sql")
    elif state.get("generated_sql"):
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
