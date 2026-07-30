"""LangGraph assembly: one typed StateGraph for the full agent pipeline.

Public API:
  build_graph(checkpointer=None) -> CompiledGraph
  ask(question, *, thread_id=None) -> AskResponse
  resume(reply, *, thread_id) -> AskResponse

LangGraph primitives used:
  - StateGraph with AgentState TypedDict (typed state, Annotated reducers)
  - InMemorySaver for thread-scoped checkpoints
  - interrupt() to pause for user clarification; Command(resume=) to continue
  - add_node / add_edge / add_conditional_edges for fixed and dynamic routing
  - graph.invoke() with configurable.thread_id for thread isolation
"""

from __future__ import annotations

import time
import uuid

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agent_host.budget import budget_from_env
from agent_host.nodes.answer_nodes import documentation_answer_node, general_answer_node
from agent_host.nodes.exploration_nodes import exploration_node
from agent_host.nodes.intent_nodes import REFUSAL_INTENTS, classify_intent_node
from agent_host.nodes.lifecycle_nodes import (
    bounded_followup_node,
    final_answer_node,
    interpretation_and_citations_node,
)
from agent_host.nodes.output_safety_nodes import classify_output_safety_node
from agent_host.nodes.policy_nodes import input_policy_node, result_safety_node
from agent_host.nodes.retrieval_nodes import (
    context_gate_node,
    retrieval_permission_node,
    retrieve_context_node,
)
from agent_host.nodes.sql_nodes import (
    execution_not_configured_node,
    fix_sql_node,
    plan_safety_node,
    query_plan_node,
    validate_sql_node,
    write_sql_node,
)
from agent_host.schemas import AskResponse
from agent_host.state import AgentContext, AgentState, make_initial_state

# ── routing functions ─────────────────────────────────────────────────────────


def _route_from_policy(state: AgentState) -> str:
    if state.get("policy_blocked"):
        return "final_answer"
    return "classify_intent"


def _route_from_classify_intent(state: AgentState) -> str:
    intent = state.get("intent")
    if intent is None:
        # Returned from clarification interrupt — restart from policy check
        return "input_policy"
    if intent in REFUSAL_INTENTS:
        return "result_safety"
    if intent == "general_question":
        return "general_answer"
    if intent == "unknown":
        # Clarification exhausted — answer already set
        return "result_safety"
    return "retrieval_permission"


def _route_from_retrieval_permission(state: AgentState) -> str:
    if state.get("intent") in {"table_discovery", "schema_lookup", "aggregate_definition"}:
        return "exploration"
    return "retrieve_context"


def _route_from_context_gate(state: AgentState) -> str:
    intent = state.get("intent")
    answer = state.get("answer")

    if intent is None:
        # Clarification interrupt — restart from policy
        return "input_policy"

    if answer:
        # No context / clarification exhausted — answer already set
        return "result_safety"

    if intent == "safe_sql_generation":
        return "query_plan"

    return "documentation_answer"


def _route_from_query_plan(state: AgentState) -> str:
    if state.get("answer"):
        return "result_safety"
    return "plan_safety"


def _route_from_plan_safety(state: AgentState) -> str:
    if state.get("answer"):
        return "result_safety"
    return "write_sql"


def _route_from_write_sql(state: AgentState) -> str:
    if state.get("answer"):
        return "result_safety"
    if state.get("candidate_sql") or state.get("generated_sql"):
        return "validate_sql"
    return "result_safety"


def _route_from_fix_sql(state: AgentState) -> str:
    if state.get("answer"):
        return "result_safety"
    if state.get("candidate_sql"):
        return "validate_sql"
    return "result_safety"


def _route_from_validate_sql(state: AgentState) -> str:
    if state.get("answer"):
        return "result_safety"
    raw_validation = state.get("validation_result")
    if not raw_validation:
        # No validation result means an error; answer is already set
        return "result_safety"

    from sql.models import SqlValidationResult

    result = SqlValidationResult.model_validate(raw_validation)
    budget = budget_from_env()

    if result.allowed:
        return "execution_not_configured"

    repair_count = state.get("repair_count", 0)
    if result.is_repairable and repair_count < budget.max_sql_repairs:
        return "fix_sql"

    # Failed or exhausted — answer already set in validate_sql_node
    return "result_safety"


# Compatibility alias for existing imports while the node name migrates.
_route_from_generate_sql = _route_from_write_sql


# ── graph construction ────────────────────────────────────────────────────────


def build_graph(checkpointer=None):
    """Build and compile the agent pipeline graph.

    Args:
        checkpointer: LangGraph checkpoint backend. Defaults to InMemorySaver.

    Returns:
        Compiled LangGraph graph ready to invoke.
    """
    if checkpointer is None:
        checkpointer = InMemorySaver()

    builder = StateGraph(AgentState, context_schema=AgentContext)

    # Register nodes
    builder.add_node("input_policy", input_policy_node)
    builder.add_node("classify_intent", classify_intent_node)
    builder.add_node("general_answer", general_answer_node)
    builder.add_node("retrieval_permission", retrieval_permission_node)
    builder.add_node("exploration", exploration_node)
    builder.add_node("retrieve_context", retrieve_context_node)
    builder.add_node("context_gate", context_gate_node)
    builder.add_node("documentation_answer", documentation_answer_node)
    builder.add_node("query_plan", query_plan_node)
    builder.add_node("plan_safety", plan_safety_node)
    builder.add_node("write_sql", write_sql_node)
    builder.add_node("fix_sql", fix_sql_node)
    builder.add_node("validate_sql", validate_sql_node)
    builder.add_node("execution_not_configured", execution_not_configured_node)
    builder.add_node("classify_output_safety", classify_output_safety_node)
    builder.add_node("result_safety", result_safety_node)
    builder.add_node("interpretation_and_citations", interpretation_and_citations_node)
    builder.add_node("final_answer", final_answer_node)
    builder.add_node("bounded_followup", bounded_followup_node)

    # Fixed edges
    builder.add_edge(START, "input_policy")
    builder.add_edge("exploration", "context_gate")
    builder.add_edge("retrieve_context", "context_gate")
    builder.add_edge("general_answer", "classify_output_safety")
    builder.add_edge("documentation_answer", "classify_output_safety")
    builder.add_edge("execution_not_configured", "classify_output_safety")
    builder.add_edge("classify_output_safety", "result_safety")
    builder.add_edge("result_safety", "interpretation_and_citations")
    builder.add_edge("interpretation_and_citations", "final_answer")
    builder.add_edge("final_answer", "bounded_followup")
    builder.add_edge("bounded_followup", END)

    # Conditional edges
    builder.add_conditional_edges(
        "input_policy",
        _route_from_policy,
        {"final_answer": "final_answer", "classify_intent": "classify_intent"},
    )
    builder.add_conditional_edges(
        "classify_intent",
        _route_from_classify_intent,
        {
            "input_policy": "input_policy",
            "result_safety": "result_safety",
            "general_answer": "general_answer",
            "retrieval_permission": "retrieval_permission",
        },
    )
    builder.add_conditional_edges(
        "retrieval_permission",
        _route_from_retrieval_permission,
        {"exploration": "exploration", "retrieve_context": "retrieve_context"},
    )
    builder.add_conditional_edges(
        "context_gate",
        _route_from_context_gate,
        {
            "input_policy": "input_policy",
            "result_safety": "result_safety",
            "documentation_answer": "documentation_answer",
            "query_plan": "query_plan",
        },
    )
    builder.add_conditional_edges(
        "query_plan",
        _route_from_query_plan,
        {"result_safety": "result_safety", "plan_safety": "plan_safety"},
    )
    builder.add_conditional_edges(
        "plan_safety",
        _route_from_plan_safety,
        {"result_safety": "result_safety", "write_sql": "write_sql"},
    )
    builder.add_conditional_edges(
        "write_sql",
        _route_from_write_sql,
        {
            "result_safety": "result_safety",
            "validate_sql": "validate_sql",
        },
    )
    builder.add_conditional_edges(
        "fix_sql",
        _route_from_fix_sql,
        {
            "result_safety": "result_safety",
            "validate_sql": "validate_sql",
        },
    )
    builder.add_conditional_edges(
        "validate_sql",
        _route_from_validate_sql,
        {
            "execution_not_configured": "execution_not_configured",
            "fix_sql": "fix_sql",
            "result_safety": "result_safety",
        },
    )

    return builder.compile(checkpointer=checkpointer)


# ── module-level singleton ────────────────────────────────────────────────────

_graph = None
_thread_contexts: dict[str, AgentContext] = {}


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


# ── public API ────────────────────────────────────────────────────────────────


def ask(
    question: str,
    *,
    thread_id: str | None = None,
) -> AskResponse:
    """Run a new question through the agent pipeline.

    Args:
        question: User's question string.
        thread_id: Optional thread ID for conversation continuity. A new ID is
            generated if not provided.

    Returns:
        AskResponse. If interrupted for clarification, interrupted=True and
        clarification_prompt is set; the caller should call resume() with
        the user's reply and the same thread_id.
    """
    graph = _get_graph()
    tid = thread_id or uuid.uuid4().hex
    run_id = uuid.uuid4().hex
    started_at = time.monotonic()

    initial_state = make_initial_state(question, run_id=run_id, started_at=started_at)
    config: RunnableConfig = {"configurable": {"thread_id": tid}}
    context = AgentContext(budget=budget_from_env())
    _thread_contexts[tid] = context

    try:
        result = graph.invoke(initial_state, config=config, context=context)
    except Exception as exc:
        # Check for LangGraph interrupt (some versions raise instead of returning)
        if "GraphInterrupt" in type(exc).__name__ or hasattr(exc, "interrupt_value"):
            prompt = str(getattr(exc, "interrupt_value", str(exc)))
            return AskResponse(
                answer="",
                used_tools=[],
                run_id=run_id,
                trace_file="",
                thread_id=tid,
                interrupted=True,
                clarification_prompt=prompt,
            )
        raise

    response = _result_to_response(result, run_id, tid)
    if not response.interrupted:
        _thread_contexts.pop(tid, None)
    return response


def resume(
    reply: str,
    *,
    thread_id: str,
) -> AskResponse:
    """Resume an interrupted graph with the user's clarification reply.

    Args:
        reply: User's response to the clarification prompt.
        thread_id: Thread ID from the interrupted ask() call.

    Returns:
        AskResponse with the completed answer, or another interruption.
    """
    graph = _get_graph()
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    context = _thread_contexts.get(thread_id) or AgentContext(budget=budget_from_env())
    _thread_contexts[thread_id] = context

    # Recover run_id from checkpoint state
    try:
        checkpoint_state = graph.get_state(config)
        run_id = checkpoint_state.values.get("run_id", uuid.uuid4().hex)
    except Exception:
        run_id = uuid.uuid4().hex

    try:
        result = graph.invoke(Command(resume=reply), config=config, context=context)
    except Exception as exc:
        if "GraphInterrupt" in type(exc).__name__ or hasattr(exc, "interrupt_value"):
            prompt = str(getattr(exc, "interrupt_value", str(exc)))
            return AskResponse(
                answer="",
                used_tools=[],
                run_id=run_id,
                trace_file="",
                thread_id=thread_id,
                interrupted=True,
                clarification_prompt=prompt,
            )
        raise

    response = _result_to_response(result, run_id, thread_id)
    if not response.interrupted:
        _thread_contexts.pop(thread_id, None)
    return response


def _result_to_response(result: dict, run_id: str, thread_id: str) -> AskResponse:
    # Detect interrupt in result dict (LangGraph 1.x convention)
    if isinstance(result, dict) and "__interrupt__" in result:
        interrupts = result["__interrupt__"]
        prompt = str(interrupts[0].value) if interrupts else "Please clarify your request."
        return AskResponse(
            answer="",
            used_tools=[],
            run_id=result.get("run_id", run_id),
            trace_file=result.get("trace_file") or "",
            thread_id=thread_id,
            interrupted=True,
            clarification_prompt=prompt,
        )

    answer = str(result.get("answer") or "")
    used_tools: list[str] = []
    if result.get("retrieved_chunks"):
        used_tools.append("retrieve_documentation_context")
    raw_compiled = result.get("compiled_query") or {}
    if raw_compiled.get("source") == "deterministic":
        used_tools.append("compile_sql")
    elif raw_compiled.get("source") == "claude_repair":
        used_tools.append("fix_sql")
    elif result.get("generated_sql"):
        used_tools.append("generate_sql")
    if result.get("validation_result"):
        used_tools.append("validate_sql")

    return AskResponse(
        answer=answer,
        used_tools=used_tools,
        run_id=result.get("run_id", run_id),
        trace_file=result.get("trace_file") or "",
        thread_id=thread_id,
        allowed=not bool(result.get("policy_blocked")),
        policy_reason=result.get("policy_reason"),
        intent=result.get("intent"),
        intent_confidence=result.get("intent_confidence"),
        disclosure_status=result.get("execution_status"),
    )
