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
