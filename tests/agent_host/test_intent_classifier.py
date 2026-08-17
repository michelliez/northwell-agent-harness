"""Tests for intent classification contract enforcement (no model calls)."""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from agent_host.nodes.intent_nodes import (
    _INTENT_TOOL,
    EXPECTED_ACTION,
    REFUSAL_INTENTS,
    RETRIEVAL_INTENTS,
    IntentName,
    _RawIntentDecision,
    enforce_intent_contract,
)


# Re-expose enforce_intent_contract for test convenience
def _enforce(intent, confidence, needs_clarification=False, risk_flags=None, min_conf=0.70):
    class _Decision:
        pass

    d = _Decision()
    d.intent = intent
    d.confidence = confidence
    d.needs_clarification = needs_clarification
    d.risk_flags = risk_flags or []
    return enforce_intent_contract(d, min_confidence=min_conf)


def test_refusal_intent_maps_to_refuse_action() -> None:
    for intent in REFUSAL_INTENTS:
        result = _enforce(intent, 0.95)
        assert result["recommended_action"] == "refuse"
        assert result["needs_clarification"] is False


@pytest.mark.parametrize(
    "intent",
    [
        "prohibited_phi_request",
        "prompt_injection_attempt",
        "jailbreak_attempt",
        "destructive_sql_request",
    ],
)
def test_explicit_prohibited_intents_are_closed_vocabulary_refusals(intent: str) -> None:
    parsed = _RawIntentDecision.model_validate(_decision(intent=intent))
    result = enforce_intent_contract(parsed)

    assert intent in REFUSAL_INTENTS
    assert result["intent"] == intent
    assert result["recommended_action"] == "refuse"


def test_low_confidence_becomes_clarify() -> None:
    result = _enforce("documentation_lookup", confidence=0.50, min_conf=0.70)
    assert result["intent"] == "unknown"
    assert result["recommended_action"] == "clarify"
    assert result["needs_clarification"] is True
    assert "low_confidence" in result["risk_flags"]


def test_unknown_intent_becomes_clarify() -> None:
    result = _enforce("unknown", confidence=0.90, needs_clarification=True)
    assert result["intent"] == "unknown"
    assert result["recommended_action"] == "clarify"


def test_safe_intent_maps_to_correct_action() -> None:
    for intent, action in EXPECTED_ACTION.items():
        if intent in REFUSAL_INTENTS or intent == "unknown":
            continue
        result = _enforce(intent, confidence=0.90)
        assert result["recommended_action"] == action, f"Wrong action for {intent}"
        assert result["intent"] == intent


def test_retrieval_intents_include_sql_generation() -> None:
    assert "safe_sql_generation" in RETRIEVAL_INTENTS
    assert "documentation_lookup" in RETRIEVAL_INTENTS
    assert "table_discovery" in RETRIEVAL_INTENTS


def test_refusal_intents_do_not_overlap_with_retrieval_intents() -> None:
    assert REFUSAL_INTENTS.isdisjoint(RETRIEVAL_INTENTS)


def test_needs_clarification_true_maps_to_clarify() -> None:
    result = _enforce("documentation_lookup", confidence=0.95, needs_clarification=True)
    assert result["intent"] == "unknown"
    assert result["recommended_action"] == "clarify"


# --- closed-vocabulary validation -------------------------------------------


def _decision(**overrides):
    payload = {
        "intent": "documentation_lookup",
        "confidence": 0.9,
        "risk_flags": [],
        "needs_clarification": False,
    }
    payload.update(overrides)
    return payload


def test_valid_decision_parses() -> None:
    assert _RawIntentDecision.model_validate(_decision()).intent == "documentation_lookup"


def test_intent_outside_the_closed_vocabulary_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _RawIntentDecision.model_validate(_decision(intent="execute_sql"))


def test_extra_fields_are_rejected_rather_than_silently_dropped() -> None:
    with pytest.raises(ValidationError):
        _RawIntentDecision.model_validate(_decision(recommended_action="generate_sql"))


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_outside_bounds_is_rejected(confidence: float) -> None:
    with pytest.raises(ValidationError):
        _RawIntentDecision.model_validate(_decision(confidence=confidence))


def test_tool_schema_enum_matches_the_validator_vocabulary() -> None:
    """The schema shown to the model and the validator applied to it cannot drift."""
    schema_enum = _INTENT_TOOL["input_schema"]["properties"]["intent"]["enum"]
    assert list(schema_enum) == list(get_args(IntentName))


def test_every_intent_has_a_declared_action() -> None:
    assert set(EXPECTED_ACTION) == set(get_args(IntentName))


def test_resolve_candidate_reply_expands_numeric_pick() -> None:
    """A bare number resolves to the candidate text the prompt showed the user.

    Without this, the classifier receives 'User clarification: 2.' and cannot
    infer intent, looping until max_tokens. Regression for the clarification
    loop bug.
    """
    from agent_host.nodes.intent_nodes import _resolve_candidate_reply

    candidates = [
        "Which tables and columns are needed to group by department?",
        "Can you draft SQL to break down a count by department?",
        "What does the department column look like in the schema?",
    ]

    assert _resolve_candidate_reply("2", candidates) == candidates[1]
    assert _resolve_candidate_reply("2.", candidates) == candidates[1]
    assert _resolve_candidate_reply("1", candidates) == candidates[0]
    assert _resolve_candidate_reply("3", candidates) == candidates[2]


def test_resolve_candidate_reply_passes_through_prose() -> None:
    """A natural-language reply is returned unchanged."""
    from agent_host.nodes.intent_nodes import _resolve_candidate_reply

    candidates = ["Option A", "Option B"]
    reply = "I want to know about the schema structure"
    assert _resolve_candidate_reply(reply, candidates) == reply


def test_resolve_candidate_reply_handles_out_of_range_number() -> None:
    """An out-of-range number is passed through rather than crashing."""
    from agent_host.nodes.intent_nodes import _resolve_candidate_reply

    assert _resolve_candidate_reply("5", ["A", "B"]) == "5"
    assert _resolve_candidate_reply("0", ["A", "B"]) == "0"