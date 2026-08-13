"""Dead-end answers must be actionable; refusals must redirect, not coach.

Operational failures (retrieval empty, screen blocks, planner errors) tell the
user which stage stopped and suggest one concrete reformulation. Policy
refusals deliberately carry no rephrase guidance — that would be an iteration
oracle toward the boundary — but must name what the assistant does support.
Every string crosses the final-answer content screen, so it must also pass it.
"""

from __future__ import annotations

import pytest

from agent_host import failure_messages
from agent_host.nodes.intent_nodes import _REFUSAL_RESPONSES
from policy.screen import ContentSurface, screen_content

_OPERATIONAL_MESSAGES = {
    name: value
    for name, value in vars(failure_messages).items()
    if name.isupper() and isinstance(value, str)
}

# Messages a user can act on by rewording; internal-error and config-outage
# messages instead direct the user to retry or report.
_SUGGESTION_MESSAGES = [
    failure_messages.RETRIEVAL_CONTENT_BLOCKED,
    failure_messages.NO_DOCUMENTATION_FOUND,
    failure_messages.CLARIFICATION_PROMPT,
    failure_messages.CLARIFICATION_EXHAUSTED,
    failure_messages.SQL_NO_SCHEMA_EVIDENCE,
    failure_messages.SQL_PLANNER_UNAVAILABLE,
]


@pytest.mark.parametrize("message", _SUGGESTION_MESSAGES)
def test_reformulation_messages_include_a_worked_example(message: str) -> None:
    assert "example" in message.lower()
    # A worked example is a quoted question, not just the word "example".
    assert "'" in message


@pytest.mark.parametrize("name", sorted(_OPERATIONAL_MESSAGES))
def test_operational_messages_name_a_next_step(name: str) -> None:
    message = _OPERATIONAL_MESSAGES[name]
    next_step_markers = ("try", "please", "name the", "simplify", "ask again", "point me")
    assert any(marker in message.lower() for marker in next_step_markers), name


@pytest.mark.parametrize("name", sorted(_OPERATIONAL_MESSAGES))
def test_operational_messages_pass_the_final_answer_screen(name: str) -> None:
    message = _OPERATIONAL_MESSAGES[name]
    if "{code}" in message:
        message = message.format(code="multi_table_join")
    result = screen_content(message, ContentSurface.FINAL_ANSWER)
    assert result.allowed, f"{name}: {result.reason}"


@pytest.mark.parametrize("intent", sorted(_REFUSAL_RESPONSES))
def test_refusals_redirect_to_supported_capability(intent: str) -> None:
    _, answer = _REFUSAL_RESPONSES[intent]
    assert "I can" in answer, "a refusal must say what the assistant does support"


@pytest.mark.parametrize("intent", sorted(_REFUSAL_RESPONSES))
def test_refusals_never_coach_a_rephrase(intent: str) -> None:
    _, answer = _REFUSAL_RESPONSES[intent]
    lowered = answer.lower()
    for coaching_marker in ("for example", "try ", "rephras", "instead, ask", "reword"):
        assert coaching_marker not in lowered, (
            "refusal guidance must not teach the user how to iterate toward the boundary"
        )


# Refusal texts are NOT screened: intent_refusal routes directly to
# final_answer (see graph.py) precisely because a safe refusal mentioning
# "individual patients" would false-positive the row-level screen. No screen
# assertion for them here — the routing test suite owns that edge.
