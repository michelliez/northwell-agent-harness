"""Policy gate nodes: input screening and final answer screening."""

from __future__ import annotations

import time
import uuid

from agent_host.config import get_config
from agent_host.conversation import ConversationTurn, resolve_followup
from agent_host.state import AgentState
from agent_host.trace_logger import TraceLogger
from policy.screen import ContentSurface, screen_content


def input_policy_node(state: AgentState) -> dict:
    """Screen the user input. Block before any model, retrieval, or tool call."""
    cfg = get_config()

    # Initialize trace on first entry; reuse on clarification resume.
    run_id = state.get("run_id") or uuid.uuid4().hex
    trace_file = state.get("trace_file")
    if trace_file:
        trace_dir = str(trace_file).rsplit("/", 1)[0].rsplit("\\", 1)[0]
        trace = TraceLogger(
            trace_dir=trace_dir,
            run_id=run_id,
            content_mode=cfg.trace_content_mode,
        )
    else:
        trace = TraceLogger(
            trace_dir=str(cfg.trace_dir),
            run_id=run_id,
            content_mode=cfg.trace_content_mode,
        )
        trace_file = str(trace.path)

    question = state.get("question", "")
    started_at = state.get("started_at") or time.monotonic()

    screen_result = screen_content(question, ContentSurface.USER_INPUT)
    gate_result = screen_result.as_policy_result()
    trace.record(
        "policy_gate.checked",
        result=gate_result,
        surface=screen_result.surface.value,
    )

    if not screen_result.allowed:
        trace.record(
            "request.blocked",
            reason=gate_result["reason"],
            matched_term=gate_result["matched_term"],
        )
        answer = (
            "I can't help with that request because it is blocked by the "
            f"policy screen: {gate_result['reason']}."
        )
        return {
            "run_id": run_id,
            "trace_file": trace_file,
            "started_at": started_at,
            "policy_blocked": True,
            "policy_reason": gate_result["reason"],
            "answer": answer,
        }

    trace.record("policy_gate.allowed", surface=screen_result.surface.value)
    return {
        "run_id": run_id,
        "trace_file": trace_file,
        "started_at": started_at,
        "policy_blocked": False,
        "policy_reason": None,
    }


def contextualize_followup_node(state: AgentState) -> dict:
    """Resolve bounded references, then deterministically screen the new text."""
    question = state.get("question", "")
    turns = [ConversationTurn(**item) for item in state.get("conversation_turns") or []]
    resolved, changed = resolve_followup(question, turns)
    if not changed:
        return {"question_was_contextualized": False}

    screen_result = screen_content(resolved, ContentSurface.USER_INPUT)
    gate_result = screen_result.as_policy_result()
    cfg = get_config()
    trace = _open_trace(state, cfg)
    trace.record(
        "followup.contextualized",
        changed=True,
        allowed=screen_result.allowed,
    )
    if not screen_result.allowed:
        return {
            "question": resolved,
            "question_was_contextualized": True,
            "policy_blocked": True,
            "policy_reason": gate_result["reason"],
            "answer": (
                "I can't help with that follow-up because its resolved request "
                f"is blocked by the policy screen: {gate_result['reason']}."
            ),
        }
    return {
        "question": resolved,
        "question_was_contextualized": True,
        "policy_blocked": False,
        "policy_reason": None,
    }


def result_safety_node(state: AgentState) -> dict:
    """Screen the final answer before returning it to the caller."""
    answer = state.get("answer") or ""
    cfg = get_config()
    trace = _open_trace(state, cfg)

    screen_result = screen_content(answer, ContentSurface.FINAL_ANSWER)
    gate_result = screen_result.as_policy_result()
    trace.record(
        "content.screened",
        surface=screen_result.surface.value,
        location="final_answer",
        allowed=screen_result.allowed,
        reason=gate_result.get("reason"),
    )

    if not screen_result.allowed:
        trace.record(
            "answer.blocked",
            reason=gate_result.get("reason"),
            surface=screen_result.surface.value,
        )
        safe_answer = (
            "I stopped because the answer failed the final content screen: "
            f"{gate_result.get('reason', 'policy_blocked')}."
        )
        return {
            "answer": safe_answer,
            "policy_blocked": True,
            "policy_reason": f"final_screen:{gate_result.get('reason', 'blocked')}",
        }

    # The output safety classifier is probabilistic: it assesses and flags, but
    # this deterministic gate decides. Only an explicit `block` stops the answer;
    # `flag` is recorded for review and released.
    #
    # `unavailable` means the assessment never happened. It currently passes
    # through, exactly as before this state existed, but is recorded separately
    # so the failure rate is measurable. Whether an unassessed answer should be
    # released at all is a policy decision that needs that rate first.
    assessment = state.get("output_safety_assessment") or {}
    if assessment.get("recommended_action") == "block":
        risk_flags = sorted(str(flag) for flag in assessment.get("risk_flags") or [])
        trace.record(
            "answer.blocked",
            reason="output_safety_block",
            surface=screen_result.surface.value,
            risk_flags=risk_flags,
            confidence=assessment.get("confidence"),
        )
        return {
            "answer": (
                "I stopped because the drafted answer was assessed as unsafe to "
                "return. Please rephrase or narrow the request."
            ),
            "policy_blocked": True,
            "policy_reason": "output_safety_block",
        }

    action = assessment.get("recommended_action")
    trace.record(
        "result_safety.passed",
        output_safety_action=action,
        output_safety_assessed=action not in {None, "unavailable"},
        output_safety_flags=sorted(str(flag) for flag in assessment.get("risk_flags") or []),
    )
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
