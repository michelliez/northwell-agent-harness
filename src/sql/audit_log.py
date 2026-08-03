"""File-based audit logging for all SQL workflow decisions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class AuditEvent:
    """One event in the SQL workflow audit trail."""

    timestamp: str
    run_id: str
    user_id: str | None
    event_type: Literal[
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
    decision: Literal["allowed", "rejected", "error"]
    reason: str | None = None
    sql: str | None = None
    bytes_processed: int | None = None
    referenced_tables: list[str] | None = None

    def validate(self) -> None:
        """Validate that the event is well-formed."""
        if not self.timestamp:
            raise ValueError("timestamp is required")
        if not self.run_id:
            raise ValueError("run_id is required")
        if not self.event_type:
            raise ValueError("event_type is required")
        if not self.decision:
            raise ValueError("decision is required")
        # When decision is rejected or error, reason should be provided
        if self.decision in ("rejected", "error") and not self.reason:
            raise ValueError(f"decision '{self.decision}' requires a reason")


class AuditLog:
    """File-based audit log for SQL workflow events."""

    def __init__(self, log_dir: str | Path):
        """Initialize audit log with directory path.

        Args:
            log_dir: Directory to write audit logs to
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def record(self, event: AuditEvent) -> None:
        """Append audit event to log file.

        Args:
            event: AuditEvent to record

        Raises:
            ValueError: if event is malformed
        """
        event.validate()
        log_file = self.log_dir / f"{event.run_id}.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(asdict(event), default=str) + "\n")

    def get_events(self, run_id: str) -> list[AuditEvent]:
        """Read all events for a given run_id.

        Args:
            run_id: Run ID to retrieve events for

        Returns:
            List of AuditEvent objects in chronological order
        """
        log_file = self.log_dir / f"{run_id}.jsonl"
        if not log_file.exists():
            return []

        events = []
        with open(log_file) as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    events.append(AuditEvent(**data))
        return events

    def get_all_events(self) -> list[tuple[str, AuditEvent]]:
        """Read all events from all runs.

        Returns:
            List of (run_id, event) tuples in chronological order
        """
        events: list[tuple[str, AuditEvent]] = []
        for log_file in sorted(self.log_dir.glob("*.jsonl")):
            run_id = log_file.stem
            with open(log_file) as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        event = AuditEvent(**data)
                        events.append((run_id, event))
        return events

    def summary_for_run(self, run_id: str) -> dict:
        """Get a summary of decisions for a run.

        Args:
            run_id: Run ID to summarize

        Returns:
            Dict with counts of allowed/rejected/error by event_type
        """
        events = self.get_events(run_id)
        summary: dict[str, dict[str, int]] = {}
        for event in events:
            if event.event_type not in summary:
                summary[event.event_type] = {"allowed": 0, "rejected": 0, "error": 0}
            summary[event.event_type][event.decision] += 1
        return summary
