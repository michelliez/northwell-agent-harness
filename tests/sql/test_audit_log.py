"""Tests for audit logging module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sql.audit_log import AuditEvent, AuditLog


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_audit_dir(tmp_path: Path) -> Path:
    """Create a temporary audit log directory."""
    return tmp_path / "audit_logs"


@pytest.fixture
def audit_log(tmp_audit_dir: Path) -> AuditLog:
    """Create an AuditLog instance."""
    return AuditLog(tmp_audit_dir)


@pytest.fixture
def sample_event() -> AuditEvent:
    """Create a sample audit event."""
    return AuditEvent(
        timestamp="2026-07-30T12:00:00Z",
        run_id="run-123",
        user_id="user-456",
        event_type="policy_check",
        decision="allowed",
    )


# ── Event Validation Tests ────────────────────────────────────────────────


def test_event_validates_required_fields() -> None:
    """Test that AuditEvent requires all required fields."""
    valid_event = AuditEvent(
        timestamp="2026-07-30T12:00:00Z",
        run_id="run-123",
        user_id="user-456",
        event_type="policy_check",
        decision="allowed",
    )
    # Should not raise
    valid_event.validate()


def test_event_rejects_missing_timestamp() -> None:
    """Test that AuditEvent rejects missing timestamp."""
    event = AuditEvent(
        timestamp="",
        run_id="run-123",
        user_id="user-456",
        event_type="policy_check",
        decision="allowed",
    )
    with pytest.raises(ValueError, match="timestamp"):
        event.validate()


def test_event_rejects_missing_run_id() -> None:
    """Test that AuditEvent rejects missing run_id."""
    event = AuditEvent(
        timestamp="2026-07-30T12:00:00Z",
        run_id="",
        user_id="user-456",
        event_type="policy_check",
        decision="allowed",
    )
    with pytest.raises(ValueError, match="run_id"):
        event.validate()


def test_event_allows_missing_user_id() -> None:
    """Test that AuditEvent allows missing user_id."""
    event = AuditEvent(
        timestamp="2026-07-30T12:00:00Z",
        run_id="run-123",
        user_id=None,
        event_type="policy_check",
        decision="allowed",
    )
    # Should not raise
    event.validate()


def test_event_requires_reason_for_rejected_decision() -> None:
    """Test that rejected decisions require a reason."""
    event = AuditEvent(
        timestamp="2026-07-30T12:00:00Z",
        run_id="run-123",
        user_id="user-456",
        event_type="policy_check",
        decision="rejected",
        reason=None,
    )
    with pytest.raises(ValueError, match="requires a reason"):
        event.validate()


def test_event_requires_reason_for_error_decision() -> None:
    """Test that error decisions require a reason."""
    event = AuditEvent(
        timestamp="2026-07-30T12:00:00Z",
        run_id="run-123",
        user_id="user-456",
        event_type="policy_check",
        decision="error",
        reason=None,
    )
    with pytest.raises(ValueError, match="requires a reason"):
        event.validate()


def test_event_allows_missing_reason_for_allowed_decision() -> None:
    """Test that allowed decisions don't require a reason."""
    event = AuditEvent(
        timestamp="2026-07-30T12:00:00Z",
        run_id="run-123",
        user_id="user-456",
        event_type="policy_check",
        decision="allowed",
        reason=None,
    )
    # Should not raise
    event.validate()


# ── Recording Tests ───────────────────────────────────────────────────────


def test_record_creates_log_file(audit_log: AuditLog, sample_event: AuditEvent) -> None:
    """Test that recording creates a log file."""
    audit_log.record(sample_event)

    log_file = audit_log.log_dir / "run-123.jsonl"
    assert log_file.exists()


def test_record_appends_json_line(audit_log: AuditLog, sample_event: AuditEvent) -> None:
    """Test that recording appends JSONL format."""
    audit_log.record(sample_event)

    log_file = audit_log.log_dir / "run-123.jsonl"
    with open(log_file) as f:
        line = f.readline()
    data = json.loads(line)
    assert data["run_id"] == "run-123"
    assert data["event_type"] == "policy_check"
    assert data["decision"] == "allowed"


def test_record_validates_event(audit_log: AuditLog) -> None:
    """Test that recording validates the event first."""
    event = AuditEvent(
        timestamp="",
        run_id="run-123",
        user_id="user-456",
        event_type="policy_check",
        decision="allowed",
    )
    with pytest.raises(ValueError, match="timestamp"):
        audit_log.record(event)


def test_record_appends_to_existing_file(audit_log: AuditLog) -> None:
    """Test that recording appends to existing log files."""
    event1 = AuditEvent(
        timestamp="2026-07-30T12:00:00Z",
        run_id="run-123",
        user_id="user-456",
        event_type="policy_check",
        decision="allowed",
    )
    event2 = AuditEvent(
        timestamp="2026-07-30T12:00:01Z",
        run_id="run-123",
        user_id="user-456",
        event_type="intent_classified",
        decision="allowed",
    )
    audit_log.record(event1)
    audit_log.record(event2)

    log_file = audit_log.log_dir / "run-123.jsonl"
    with open(log_file) as f:
        lines = f.readlines()
    assert len(lines) == 2


# ── Retrieval Tests ───────────────────────────────────────────────────────


def test_get_events_returns_empty_list_for_missing_run(audit_log: AuditLog) -> None:
    """Test that get_events returns empty list for non-existent run."""
    events = audit_log.get_events("nonexistent")
    assert events == []


def test_get_events_returns_all_events_for_run(audit_log: AuditLog) -> None:
    """Test that get_events returns all events for a run."""
    event1 = AuditEvent(
        timestamp="2026-07-30T12:00:00Z",
        run_id="run-123",
        user_id="user-456",
        event_type="policy_check",
        decision="allowed",
    )
    event2 = AuditEvent(
        timestamp="2026-07-30T12:00:01Z",
        run_id="run-123",
        user_id="user-456",
        event_type="intent_classified",
        decision="allowed",
    )
    audit_log.record(event1)
    audit_log.record(event2)

    events = audit_log.get_events("run-123")
    assert len(events) == 2
    assert events[0].event_type == "policy_check"
    assert events[1].event_type == "intent_classified"


def test_get_events_preserves_order(audit_log: AuditLog) -> None:
    """Test that get_events returns events in recording order."""
    for i in range(5):
        event = AuditEvent(
            timestamp=f"2026-07-30T12:00:0{i}Z",
            run_id="run-123",
            user_id="user-456",
            event_type="policy_check",
            decision="allowed",
        )
        audit_log.record(event)

    events = audit_log.get_events("run-123")
    assert len(events) == 5
    for i, event in enumerate(events):
        assert event.timestamp == f"2026-07-30T12:00:0{i}Z"


def test_get_all_events_returns_events_from_all_runs(audit_log: AuditLog) -> None:
    """Test that get_all_events returns events from all runs."""
    for run_id in ["run-1", "run-2", "run-3"]:
        event = AuditEvent(
            timestamp="2026-07-30T12:00:00Z",
            run_id=run_id,
            user_id="user-456",
            event_type="policy_check",
            decision="allowed",
        )
        audit_log.record(event)

    all_events = audit_log.get_all_events()
    assert len(all_events) == 3
    run_ids = {run_id for run_id, _ in all_events}
    assert run_ids == {"run-1", "run-2", "run-3"}


def test_get_all_events_returns_empty_list_for_empty_directory(
    audit_log: AuditLog,
) -> None:
    """Test that get_all_events returns empty list when no log files exist."""
    all_events = audit_log.get_all_events()
    assert all_events == []


# ── Summary Tests ─────────────────────────────────────────────────────────


def test_summary_for_run_counts_decisions(audit_log: AuditLog) -> None:
    """Test that summary_for_run counts decisions by event type."""
    events = [
        AuditEvent(
            timestamp="2026-07-30T12:00:00Z",
            run_id="run-123",
            user_id="user-456",
            event_type="policy_check",
            decision="allowed",
        ),
        AuditEvent(
            timestamp="2026-07-30T12:00:01Z",
            run_id="run-123",
            user_id="user-456",
            event_type="policy_check",
            decision="rejected",
            reason="low confidence",
        ),
        AuditEvent(
            timestamp="2026-07-30T12:00:02Z",
            run_id="run-123",
            user_id="user-456",
            event_type="intent_classified",
            decision="allowed",
        ),
    ]
    for event in events:
        audit_log.record(event)

    summary = audit_log.summary_for_run("run-123")

    assert summary["policy_check"]["allowed"] == 1
    assert summary["policy_check"]["rejected"] == 1
    assert summary["policy_check"]["error"] == 0
    assert summary["intent_classified"]["allowed"] == 1


def test_summary_for_nonexistent_run_returns_empty_dict(audit_log: AuditLog) -> None:
    """Test that summary_for_run returns empty dict for non-existent run."""
    summary = audit_log.summary_for_run("nonexistent")
    assert summary == {}


def test_summary_handles_all_event_types(audit_log: AuditLog) -> None:
    """Test that summary handles all event types."""
    event_types = [
        "policy_check",
        "intent_classified",
        "retrieval",
        "plan_safety",
        "sql_compiled",
        "dry_run",
        "cost_gate",
        "result_safety",
        "execution",
    ]
    for event_type in event_types:
        event = AuditEvent(
            timestamp="2026-07-30T12:00:00Z",
            run_id="run-123",
            user_id="user-456",
            event_type=event_type,
            decision="allowed",
        )
        audit_log.record(event)

    summary = audit_log.summary_for_run("run-123")
    assert len(summary) == 9
    assert all(event_type in summary for event_type in event_types)


# ── Integration Tests ─────────────────────────────────────────────────────


def test_full_workflow_audit_trail(audit_log: AuditLog) -> None:
    """Test a complete audit trail for one query."""
    run_id = "run-abc123"
    user_id = "user-jane"

    # Simulate a complete workflow
    events = [
        AuditEvent(
            timestamp="2026-07-30T12:00:00Z",
            run_id=run_id,
            user_id=user_id,
            event_type="policy_check",
            decision="allowed",
        ),
        AuditEvent(
            timestamp="2026-07-30T12:00:01Z",
            run_id=run_id,
            user_id=user_id,
            event_type="intent_classified",
            decision="allowed",
        ),
        AuditEvent(
            timestamp="2026-07-30T12:00:02Z",
            run_id=run_id,
            user_id=user_id,
            event_type="retrieval",
            decision="allowed",
            referenced_tables=["APPOINTMENT_FACT", "PROVIDER"],
        ),
        AuditEvent(
            timestamp="2026-07-30T12:00:03Z",
            run_id=run_id,
            user_id=user_id,
            event_type="plan_safety",
            decision="allowed",
        ),
        AuditEvent(
            timestamp="2026-07-30T12:00:04Z",
            run_id=run_id,
            user_id=user_id,
            event_type="sql_compiled",
            decision="allowed",
            sql="SELECT COUNT(*) FROM APPOINTMENT_FACT",
        ),
        AuditEvent(
            timestamp="2026-07-30T12:00:05Z",
            run_id=run_id,
            user_id=user_id,
            event_type="dry_run",
            decision="allowed",
            bytes_processed=1_000_000,
        ),
        AuditEvent(
            timestamp="2026-07-30T12:00:06Z",
            run_id=run_id,
            user_id=user_id,
            event_type="cost_gate",
            decision="allowed",
            bytes_processed=1_000_000,
        ),
        AuditEvent(
            timestamp="2026-07-30T12:00:07Z",
            run_id=run_id,
            user_id=user_id,
            event_type="execution",
            decision="allowed",
        ),
    ]

    for event in events:
        audit_log.record(event)

    # Retrieve and verify
    recorded_events = audit_log.get_events(run_id)
    assert len(recorded_events) == 8

    # Check order
    assert recorded_events[0].event_type == "policy_check"
    assert recorded_events[-1].event_type == "execution"

    # Check details
    retrieval_event = recorded_events[2]
    assert retrieval_event.referenced_tables == ["APPOINTMENT_FACT", "PROVIDER"]

    dry_run_event = recorded_events[5]
    assert dry_run_event.bytes_processed == 1_000_000


def test_multiple_runs_separate_logs(audit_log: AuditLog) -> None:
    """Test that multiple runs maintain separate audit logs."""
    for run_id in ["run-1", "run-2"]:
        for i in range(3):
            event = AuditEvent(
                timestamp=f"2026-07-30T12:00:{i:02d}Z",
                run_id=run_id,
                user_id="user-456",
                event_type="policy_check",
                decision="allowed",
            )
            audit_log.record(event)

    # Verify separation
    run_1_events = audit_log.get_events("run-1")
    run_2_events = audit_log.get_events("run-2")

    assert len(run_1_events) == 3
    assert len(run_2_events) == 3
    assert all(e.run_id == "run-1" for e in run_1_events)
    assert all(e.run_id == "run-2" for e in run_2_events)


# ── Optional Fields Tests ─────────────────────────────────────────────────


def test_event_with_all_optional_fields() -> None:
    """Test AuditEvent with all optional fields populated."""
    event = AuditEvent(
        timestamp="2026-07-30T12:00:00Z",
        run_id="run-123",
        user_id="user-456",
        event_type="dry_run",
        decision="allowed",
        reason="Cost within limits",
        sql="SELECT * FROM TABLE WHERE id = @param",
        bytes_processed=5_000_000,
        referenced_tables=["TABLE1", "TABLE2"],
    )
    event.validate()  # Should not raise


def test_event_with_minimal_fields() -> None:
    """Test AuditEvent with only required fields."""
    event = AuditEvent(
        timestamp="2026-07-30T12:00:00Z",
        run_id="run-123",
        user_id=None,
        event_type="policy_check",
        decision="allowed",
    )
    event.validate()  # Should not raise


def test_rejected_decision_with_minimal_reason(audit_log: AuditLog) -> None:
    """Test recording rejected decisions with brief reasons."""
    event = AuditEvent(
        timestamp="2026-07-30T12:00:00Z",
        run_id="run-123",
        user_id="user-456",
        event_type="cost_gate",
        decision="rejected",
        reason="exceeds limit",
    )
    audit_log.record(event)

    recorded = audit_log.get_events("run-123")
    assert recorded[0].reason == "exceeds limit"


def test_error_decision_includes_error_reason(audit_log: AuditLog) -> None:
    """Test recording error decisions with error messages."""
    event = AuditEvent(
        timestamp="2026-07-30T12:00:00Z",
        run_id="run-123",
        user_id="user-456",
        event_type="execution",
        decision="error",
        reason="Query execution timeout after 30s",
    )
    audit_log.record(event)

    recorded = audit_log.get_events("run-123")
    assert recorded[0].decision == "error"
    assert "timeout" in recorded[0].reason.lower()
