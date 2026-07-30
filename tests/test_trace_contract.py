"""The trace contract must stay closed and in sync with what nodes emit.

The failure this guards against already happened once: nodes, the trace viewer,
and the graders each kept their own list of event names and silently diverged.
The scan below reads the actual `trace.record(...)` call sites out of the source
so a new event cannot be emitted without being registered.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from agent_host.trace_contract import (
    DOWNSTREAM_EVENTS,
    EVENT_NAMES,
    EVENT_SPEC,
    GATE_EVENT,
    INTENT_EVENT,
    WORK_START_EVENTS,
    EventStatus,
    TraceDomain,
    TraceEventName,
    UnknownTraceEvent,
    domain_for,
    spec_for,
)
from agent_host.trace_logger import _TRACE_ADAPTER

SRC = Path(__file__).resolve().parents[1] / "src"
_RECORD_CALL = re.compile(r"\.record\(\s*\"([a-z_][a-z_.]*)\"", re.MULTILINE)


def _emitted_event_names() -> set[str]:
    names: set[str] = set()
    for path in SRC.rglob("*.py"):
        if not path.is_file():
            continue
        names.update(_RECORD_CALL.findall(path.read_text(encoding="utf-8")))
    return names


# --- vocabulary integrity ----------------------------------------------------


def test_every_declared_name_has_a_spec() -> None:
    assert set(get_args(TraceEventName)) == set(EVENT_SPEC)


def test_no_duplicate_names_in_the_vocabulary() -> None:
    declared = list(get_args(TraceEventName))
    assert len(declared) == len(set(declared))


def test_specs_use_declared_kinds_and_statuses() -> None:
    kinds, statuses = set(get_args(TraceDomain)), set(get_args(EventStatus))
    for name, spec in EVENT_SPEC.items():
        assert spec.domain in kinds, f"{name} has kind {spec.domain!r}"
        assert spec.status in statuses, f"{name} has status {spec.status!r}"
        assert spec.label, f"{name} has no label"


# --- the contract matches reality -------------------------------------------


def test_every_emitted_event_is_registered() -> None:
    """A node cannot record an event the viewer and graders do not know."""
    unregistered = sorted(_emitted_event_names() - EVENT_NAMES)
    assert not unregistered, (
        f"emitted but not in the contract: {unregistered}. "
        "Add each to TraceEventName and EVENT_SPEC."
    )


def test_contract_has_no_events_nothing_emits() -> None:
    """Catches names left behind after a node is deleted or renamed."""
    orphans = sorted(EVENT_NAMES - _emitted_event_names())
    assert not orphans, f"registered but never emitted: {orphans}"


# --- derived sets ------------------------------------------------------------


def test_grader_anchors_are_real_events() -> None:
    for anchor in (GATE_EVENT, INTENT_EVENT, *WORK_START_EVENTS):
        assert anchor in EVENT_NAMES


def test_downstream_events_exclude_the_policy_gate() -> None:
    """A blocked run still emits gate events; those must not count as downstream."""
    assert GATE_EVENT not in DOWNSTREAM_EVENTS
    assert not any(EVENT_SPEC[name].domain == "policy" for name in DOWNSTREAM_EVENTS)


def test_work_start_events_are_downstream() -> None:
    assert WORK_START_EVENTS <= DOWNSTREAM_EVENTS


# --- lookup and record validation -------------------------------------------


def test_spec_for_rejects_an_unknown_event() -> None:
    with pytest.raises(UnknownTraceEvent):
        spec_for("model.request")


def _row(event: str, **extra):
    return {
        "ts": 0.0,
        "seq": 0,
        "run_id": "r",
        "event": event,
        "domain": domain_for(event),
        **extra,
    }


def test_written_record_validates_against_the_contract() -> None:
    record = _TRACE_ADAPTER.validate_python(_row("validate_sql.failed_final"))
    assert record.domain == "sql"


def test_record_rejects_an_off_contract_event() -> None:
    with pytest.raises(ValidationError):
        _TRACE_ADAPTER.validate_python(
            {"ts": 0.0, "seq": 0, "run_id": "r", "event": "model.request", "domain": "sql"}
        )


def test_record_rejects_a_domain_that_contradicts_the_registry() -> None:
    """A line must not claim a domain the contract does not assign it."""
    record = _TRACE_ADAPTER.validate_python(_row("answer.ready"))
    assert record.domain == "lifecycle"


def test_answer_ready_is_lifecycle_not_policy() -> None:
    """The old prefix rule mapped every `answer.*` event to policy.

    `answer.blocked` really is a policy stop, but `answer.ready` is the run
    completing. Deriving from the registry separates them.
    """
    assert domain_for("answer.ready") == "lifecycle"
    assert domain_for("answer.blocked") == "policy"


def test_payload_fields_are_still_permitted() -> None:
    """Events carry event-specific payload; only the envelope is fixed."""
    record = _TRACE_ADAPTER.validate_python(_row("retrieval.completed", chunk_count=5))
    assert record.domain == "retrieval"
