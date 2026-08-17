"""Conversation history for multi-turn continuity.

Each completed turn stores the user question, the cleaned policy-screened
answer, catalog anchors (table/column names) for deixis resolution, and the
offered suggested follow-up question. The full Q&A pair flows into model calls
as a standard messages array so the classifier, planner, and answer nodes all
share the same conversational context — the same design any chat application
uses.

The Clarity Dictionary is Epic's first-party schema documentation and is
treated as authoritative ground truth. Policy-screened answers derived from it
are safe to carry forward in conversation history. Raw retrieved chunks, SQL
results, and execution outputs are never stored here; only the final
cleaned answer that already passed the output content screen is kept.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

_TABLE_REFERENCE = re.compile(r"\b(?:this|that|the)\s+table\b", re.IGNORECASE)
_COLUMN_REFERENCE = re.compile(r"\b(?:this|that|the)\s+column\b", re.IGNORECASE)
_CATALOG_IDENTIFIER = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
_SUGGESTED_QUERY_LINE = re.compile(r"^Suggested query:\s*(.+?)\s*$", re.MULTILINE)
MAX_SUGGESTION_CHARS = 300

# A closed vocabulary, not sentiment analysis: only a reply that is nothing
# but an acceptance may be rewritten into the stored suggestion. Anything
# with additional content is a new question and resolves normally.
_ACCEPTANCE_PHRASES = frozenset(
    {
        "yes",
        "yes please",
        "yeah",
        "yep",
        "sure",
        "ok",
        "okay",
        "please",
        "please do",
        "do it",
        "do that",
        "go ahead",
        "sounds good",
        "draft it",
        "draft that",
        "generate it",
        "write it",
        "try it",
        "that one",
    }
)


@dataclass(frozen=True)
class ConversationTurn:
    """One completed turn retained for conversational continuity."""

    question: str
    answer: str = ""
    tables: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    suggested_question: str = ""


@dataclass
class _ThreadEntry:
    turns: list[ConversationTurn] = field(default_factory=list)
    expires_at: float = 0.0


class ConversationStore:
    """Thread-safe TTL store with hard bounds on threads and turns."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 1_800.0,
        max_threads: int = 1_000,
        max_turns: int = 2,
        clock=time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_threads < 1 or max_turns < 1:
            raise ValueError("conversation store limits must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_threads = max_threads
        self.max_turns = max_turns
        self._clock = clock
        self._entries: OrderedDict[str, _ThreadEntry] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, thread_id: str) -> list[ConversationTurn]:
        """Return a copy of unexpired turns and refresh no expiration."""
        with self._lock:
            self._purge_expired()
            entry = self._entries.get(thread_id)
            if entry is None:
                return []
            self._entries.move_to_end(thread_id)
            return list(entry.turns)

    def record(self, thread_id: str, turn: ConversationTurn) -> None:
        """Append one turn, evicting old turns and least-recent threads."""
        with self._lock:
            self._purge_expired()
            entry = self._entries.setdefault(thread_id, _ThreadEntry())
            entry.turns = [*entry.turns, turn][-self.max_turns :]
            entry.expires_at = self._clock() + self.ttl_seconds
            self._entries.move_to_end(thread_id)
            while len(self._entries) > self.max_threads:
                self._entries.popitem(last=False)

    def delete(self, thread_id: str) -> bool:
        """Delete a thread idempotently and report whether it existed."""
        with self._lock:
            return self._entries.pop(thread_id, None) is not None

    def _purge_expired(self) -> None:
        now = self._clock()
        expired = [key for key, value in self._entries.items() if value.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)


def resolve_followup(question: str, turns: list[ConversationTurn]) -> tuple[str, bool]:
    """Resolve explicit table/column deixis only when the latest anchor is unique.

    A reply consisting solely of an acceptance phrase resolves to the previous
    turn's suggested question, if one was offered. The caller re-screens every
    changed question through the input policy before routing on it.
    """
    if not turns:
        return question, False

    latest = turns[-1]
    if latest.suggested_question and _is_acceptance(question):
        return latest.suggested_question, True

    resolved = question
    if len(latest.tables) == 1 and _TABLE_REFERENCE.search(resolved):
        resolved = _TABLE_REFERENCE.sub(f"the {latest.tables[0]} table", resolved)
    if len(latest.columns) == 1 and _COLUMN_REFERENCE.search(resolved):
        resolved = _COLUMN_REFERENCE.sub(f"the {latest.columns[0]} column", resolved)
    return resolved, resolved != question


def turn_from_result(question: str, result: dict) -> ConversationTurn:
    """Build a conversation turn from a completed graph result."""
    explicit = _ordered_unique(_CATALOG_IDENTIFIER.findall(question))
    plan_tables = _plan_table_names(result)
    chunk_tables = _chunk_table_names(result)
    tables = explicit or plan_tables or chunk_tables
    return ConversationTurn(
        question=question,
        answer=str(result.get("answer") or "").strip(),
        tables=tuple(tables[:5]),
        suggested_question=_extract_suggestion(result),
    )


def turns_to_messages(turns: list[dict]) -> list[dict]:
    """Convert serialized ConversationTurn dicts to a messages array.

    Produces the standard [{role: user}, {role: assistant}] pairs that the
    Anthropic API expects for multi-turn conversation context. Turns without
    an answer (e.g. policy-blocked turns that were not stored) are skipped.
    """
    messages: list[dict] = []
    for turn in turns:
        q = str(turn.get("question") or "").strip()
        a = str(turn.get("answer") or "").strip()
        if q and a:
            messages.append({"role": "user", "content": q})
            messages.append({"role": "assistant", "content": a})
    return messages


def _is_acceptance(question: str) -> bool:
    normalized = " ".join(question.split()).casefold().rstrip(".!?")
    return normalized in _ACCEPTANCE_PHRASES


def _extract_suggestion(result: dict) -> str:
    """Pull the last single-line 'Suggested query:' offer out of the answer."""
    answer = str(result.get("answer") or "")
    matches = _SUGGESTED_QUERY_LINE.findall(answer)
    if not matches:
        return ""
    return matches[-1][:MAX_SUGGESTION_CHARS]


def _plan_table_names(result: dict) -> list[str]:
    raw = result.get("approved_plan") or result.get("query_plan") or {}
    plan = raw.get("plan") if isinstance(raw.get("plan"), dict) else raw
    values = plan.get("tables") if isinstance(plan, dict) else []
    names: list[str] = []
    for value in values or []:
        if isinstance(value, str):
            names.append(value.rsplit(".", 1)[-1])
        elif isinstance(value, dict):
            name = value.get("table") or value.get("name") or value.get("table_id")
            if name:
                names.append(str(name).rsplit(".", 1)[-1])
    return _ordered_unique(names)


def _chunk_table_names(result: dict) -> list[str]:
    names = []
    for chunk in result.get("retrieved_chunks") or []:
        title = str(chunk.get("title") or "").strip()
        if _CATALOG_IDENTIFIER.fullmatch(title):
            names.append(title)
    return _ordered_unique(names)


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


__all__ = [
    "ConversationStore",
    "ConversationTurn",
    "resolve_followup",
    "turn_from_result",
    "turns_to_messages",
]
