"""Retrieval pipeline nodes: permission, context retrieval, and context gate."""

from __future__ import annotations

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from agent_host.budget import budget_from_env
from agent_host.config import get_config
from agent_host.state import AgentContext, AgentState
from agent_host.trace_logger import TraceLogger
from policy.screen import ContentSurface, screen_content
from retrieval.client import RetrievedChunk, resolve_schema_evidence, retrieve_documentation

MAX_CLARIFICATION_ATTEMPTS = 3


def retrieval_permission_node(state: AgentState) -> dict:
    """Check that retrieval is permitted for this intent.

    Stub: currently always permits retrieval. Future work will check user/role
    permissions against a real authorization service.
    """
    intent = state.get("intent", "unknown")
    cfg = get_config()
    trace = _open_trace(state, cfg)
    trace.record("retrieval_permission.checked", intent=intent, permitted=True)
    return {
        "permissions": {"retrieval": True},
    }


def retrieve_context_node(
    state: AgentState,
    runtime: Runtime[AgentContext] | None = None,
) -> dict:
    """Search the SQLite RAG index and return bounded chunks."""
    cfg = get_config()
    trace = _open_trace(state, cfg)
    budget = runtime.context.budget if runtime is not None else budget_from_env()
    question = state.get("question", "")

    trace.record("retrieval.started", query=question)

    try:
        result = retrieve_documentation(
            question,
            cfg.index_path,
            budget=budget,
            top_k=budget.max_retrieved_chunks,
            dense_index_dir=cfg.dense_index_dir,
        )
    except Exception as exc:
        trace.record("retrieval.error", error=str(exc))
        return {
            "retrieved_chunks": [],
            "answer": (
                "Documentation retrieval is temporarily unavailable because the "
                "configured index could not be opened. Please contact the system "
                "administrator or try again after the index configuration is fixed."
            ),
        }

    chunks = [chunk.model_dump() for chunk in result.chunks]
    screen = screen_content(chunks, ContentSurface.TOOL_RESULT)
    if not screen.allowed:
        trace.record("retrieval.content_blocked", reason=screen.reason)
        return {
            "retrieved_chunks": [],
            "answer": "I stopped because retrieved documentation failed the content screen.",
        }
    trace.record("retrieval.completed", chunk_count=len(chunks))

    return {"retrieved_chunks": chunks}


def context_gate_node(state: AgentState) -> dict:
    """Evaluate retrieved context quality. Interrupt for clarification if weak.

    Routes:
    - No chunks + clarification budget: interrupt
    - No chunks + budget exhausted: set fallback answer
    - Has chunks + SQL intent: build schema snapshot and proceed
    - Has chunks + other intent: proceed to documentation answer
    """
    chunks = state.get("retrieved_chunks", [])
    intent = state.get("intent", "unknown")
    cfg = get_config()
    trace = _open_trace(state, cfg)

    # Operational retrieval failures already carry a safe answer. They are not
    # missing user context and must never enter the clarification loop.
    if state.get("answer"):
        return {}

    if not chunks:
        clarification_count = state.get("clarification_count", 0)
        if clarification_count >= MAX_CLARIFICATION_ATTEMPTS:
            trace.record("context_gate.no_context_exhausted")
            return {
                "answer": (
                    "I couldn't find relevant approved documentation for your question. "
                    "Please try a more specific query referencing a table or column name."
                )
            }

        trace.record("context_gate.no_context_clarification")
        new_question = interrupt(
            "I couldn't find relevant documentation. "
            "Could you clarify which table, column, or topic you're asking about?"
        )
        return {
            "question": new_question,
            "intent": None,
            "clarification_count": clarification_count + 1,
        }

    trace.record("context_gate.context_available", chunk_count=len(chunks))

    # For SQL generation, build schema snapshot from retrieved chunks
    if intent == "safe_sql_generation":
        try:
            from retrieval.client import RetrievalResult

            typed_chunks = []
            for i, c in enumerate(chunks, start=1):
                typed_chunks.append(
                    RetrievedChunk(
                        chunk_id=c["chunk_id"],
                        document_id=c["document_id"],
                        source_path=c["source_path"],
                        title=c.get("title"),
                        heading_path=c.get("heading_path"),
                        category=c.get("category"),
                        text=c["text"],
                        rank=c.get("rank", i),
                        score=c.get("score"),
                    )
                )
            retrieval_result = RetrievalResult(
                query=state.get("question", ""),
                chunks=typed_chunks,
                index_version="unknown",
            )
            snapshot = resolve_schema_evidence(retrieval_result)
            trace.record(
                "context_gate.schema_snapshot_built",
                table_count=len(snapshot.tables),
                has_unknown_safety=snapshot.has_unknown_safety(),
            )
            return {"schema_snapshot": snapshot.model_dump()}
        except Exception as exc:
            trace.record("context_gate.schema_build_error", error=str(exc))
            # An assembly failure is an internal error, not missing user
            # context: say so instead of letting query_plan blame the question.
            return {
                "schema_snapshot": None,
                "answer": (
                    "I retrieved documentation but hit an internal error while "
                    "assembling schema evidence from it, so I can't plan SQL for "
                    "this question right now. Please try again; if it persists, "
                    "this is a bug worth reporting rather than a problem with "
                    "your question."
                ),
            }

    return {}


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
