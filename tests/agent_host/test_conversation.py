from __future__ import annotations

from agent_host.conversation import (
    ConversationStore,
    ConversationTurn,
    resolve_followup,
    turn_from_result,
)


def test_resolves_unique_table_reference_without_storing_answer() -> None:
    turn = turn_from_result(
        "What is the A0H_MAP table?",
        {"answer": "Sensitive answer text that must not enter context."},
    )

    assert turn == ConversationTurn(
        question="What is the A0H_MAP table?",
        tables=("A0H_MAP",),
    )
    resolved, changed = resolve_followup("What can I use this table for?", [turn])
    assert resolved == "What can I use the A0H_MAP table for?"
    assert changed is True


def test_does_not_guess_when_latest_turn_has_multiple_tables() -> None:
    turn = ConversationTurn(question="Compare tables", tables=("A0H_MAP", "PAT_ENC"))
    resolved, changed = resolve_followup("What is this table for?", [turn])
    assert resolved == "What is this table for?"
    assert changed is False


def test_store_bounds_turns_and_expires_thread() -> None:
    now = [100.0]
    store = ConversationStore(ttl_seconds=30, max_threads=2, max_turns=2, clock=lambda: now[0])
    store.record("thread", ConversationTurn(question="one"))
    store.record("thread", ConversationTurn(question="two"))
    store.record("thread", ConversationTurn(question="three"))
    assert [turn.question for turn in store.get("thread")] == ["two", "three"]

    now[0] = 131.0
    assert store.get("thread") == []


def test_store_delete_is_idempotent() -> None:
    store = ConversationStore()
    store.record("thread", ConversationTurn(question="one"))
    assert store.delete("thread") is True
    assert store.delete("thread") is False


def test_acceptance_reply_resolves_to_stored_suggestion() -> None:
    turns = [
        ConversationTurn(
            question="What does PAT_ENC contain?",
            tables=("PAT_ENC",),
            suggested_question="Count encounters in PAT_ENC by encounter type per month",
        )
    ]

    resolved, changed = resolve_followup("yes please", turns)

    assert changed is True
    assert resolved == "Count encounters in PAT_ENC by encounter type per month"


def test_substantive_reply_is_not_treated_as_acceptance() -> None:
    turns = [
        ConversationTurn(
            question="What does PAT_ENC contain?",
            tables=("PAT_ENC",),
            suggested_question="Count encounters in PAT_ENC by encounter type per month",
        )
    ]

    resolved, changed = resolve_followup("yes but only for 2025 admissions", turns)

    assert resolved == "yes but only for 2025 admissions"
    assert changed is False


def test_acceptance_without_stored_suggestion_stays_unchanged() -> None:
    turns = [ConversationTurn(question="What does PAT_ENC contain?", tables=("PAT_ENC",))]

    resolved, changed = resolve_followup("yes", turns)

    assert resolved == "yes"
    assert changed is False


def test_turn_extracts_single_bounded_suggestion_from_answer() -> None:
    turn = turn_from_result(
        "What does PAT_ENC contain?",
        {
            "answer": (
                "PAT_ENC holds encounters. [chunk-1]\n"
                "Suggested query: Count encounters in PAT_ENC by department"
            ),
            "retrieved_chunks": [],
        },
    )

    assert turn.suggested_question == "Count encounters in PAT_ENC by department"


def test_turn_without_suggestion_line_stores_none() -> None:
    turn = turn_from_result(
        "What does PAT_ENC contain?",
        {"answer": "PAT_ENC holds encounters. [chunk-1]", "retrieved_chunks": []},
    )

    assert turn.suggested_question == ""
