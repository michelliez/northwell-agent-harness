"""Tests for metrics aggregation and reporting."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from metrics.aggregator import MetricsAggregator
from metrics.exporter import export_csv, export_json, export_markdown
from metrics.models import MetricsSummary


@pytest.fixture
def temp_artifact_dir():
    """Create a temporary artifact directory with sample data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact_path = Path(tmpdir)

        # Create trace directory with sample trace
        trace_dir = artifact_path / "traces"
        trace_dir.mkdir()

        # Create a sample trace file
        trace_file = trace_dir / "run_123.jsonl"
        now = datetime.utcnow().timestamp()
        trace_events = [
            {"ts": now, "seq": 0, "run_id": "run_123", "event": "policy_gate.checked"},
            {
                "ts": now + 0.5,
                "seq": 1,
                "run_id": "run_123",
                "event": "intent_classified",
            },
            {
                "ts": now + 1.2,
                "seq": 2,
                "run_id": "run_123",
                "event": "retrieval_completed",
            },
            {
                "ts": now + 2.0,
                "seq": 3,
                "run_id": "run_123",
                "event": "answer_generated",
            },
        ]

        with open(trace_file, "w") as f:
            for event in trace_events:
                f.write(json.dumps(event) + "\n")

        # Create audit directory with sample audit
        audit_dir = artifact_path / "audit_logs"
        audit_dir.mkdir()

        audit_file = audit_dir / "run_123.jsonl"
        today = datetime.utcnow().date().isoformat()
        audit_events = [
            {
                "timestamp": f"{today}T10:00:00",
                "run_id": "run_123",
                "user_id": "test_user",
                "event_type": "policy_check",
                "decision": "allowed",
            },
            {
                "timestamp": f"{today}T10:00:00",
                "run_id": "run_123",
                "event_type": "intent_classified",
                "decision": "allowed",
            },
            {
                "timestamp": f"{today}T10:00:01",
                "run_id": "run_123",
                "event_type": "sql_compiled",
                "decision": "allowed",
                "sql": "SELECT * FROM table",
            },
        ]

        with open(audit_file, "w") as f:
            for event in audit_events:
                f.write(json.dumps(event) + "\n")

        yield artifact_path


def test_aggregator_initialization(temp_artifact_dir: Path):
    """Test that aggregator initializes correctly."""
    aggregator = MetricsAggregator(temp_artifact_dir)
    assert aggregator.artifact_path == temp_artifact_dir
    assert aggregator.trace_dir == temp_artifact_dir / "traces"
    assert aggregator.audit_dir == temp_artifact_dir / "audit_logs"


def test_aggregator_loads_audit_logs(temp_artifact_dir: Path):
    """Test that aggregator loads audit logs correctly."""
    aggregator = MetricsAggregator(temp_artifact_dir)
    runs = aggregator._load_audit_logs(days=7)

    assert "run_123" in runs
    assert len(runs["run_123"]) > 0


def test_aggregator_loads_traces(temp_artifact_dir: Path):
    """Test that aggregator loads trace files correctly."""
    aggregator = MetricsAggregator(temp_artifact_dir)
    traces = aggregator._load_traces(days=7)

    assert "run_123" in traces
    assert len(traces["run_123"]) > 0


def test_aggregator_extracts_latency(temp_artifact_dir: Path):
    """Test latency extraction from traces."""
    aggregator = MetricsAggregator(temp_artifact_dir)
    traces = aggregator._load_traces()

    trace_events = traces.get("run_123", [])
    latency = aggregator._extract_latency(trace_events)

    assert latency > 0
    assert latency < 5000  # Should be ~2 seconds


def test_aggregator_extracts_date(temp_artifact_dir: Path):
    """Test date extraction from audit events."""
    aggregator = MetricsAggregator(temp_artifact_dir)
    runs = aggregator._load_audit_logs()

    audit_events = runs.get("run_123", [])
    date = aggregator._extract_date(audit_events)

    assert date is not None
    assert len(date) == 10  # YYYY-MM-DD


def test_aggregator_computes_summary(temp_artifact_dir: Path):
    """Test that aggregator computes metrics summary."""
    aggregator = MetricsAggregator(temp_artifact_dir)
    metrics = aggregator.get_metrics(days=7)

    assert isinstance(metrics, MetricsSummary)
    assert metrics.total_queries == 1
    assert metrics.total_latency_ms > 0
    assert metrics.policy_rejection_rate == 0  # Was allowed


def test_metrics_summary_to_dict(temp_artifact_dir: Path):
    """Test that metrics summary converts to dict correctly."""
    aggregator = MetricsAggregator(temp_artifact_dir)
    metrics = aggregator.get_metrics(days=7)

    data = metrics.to_dict()

    assert isinstance(data, dict)
    assert "total_queries" in data
    assert "by_intent" in data
    assert "by_date" in data


def test_export_json(temp_artifact_dir: Path):
    """Test JSON export."""
    aggregator = MetricsAggregator(temp_artifact_dir)
    metrics = aggregator.get_metrics(days=7)

    json_str = export_json(metrics)
    data = json.loads(json_str)

    assert "total_queries" in data
    assert data["total_queries"] == 1


def test_export_csv(temp_artifact_dir: Path):
    """Test CSV export."""
    aggregator = MetricsAggregator(temp_artifact_dir)
    metrics = aggregator.get_metrics(days=7)

    csv_str = export_csv(metrics)

    assert "Metrics Summary" in csv_str
    assert "Daily Breakdown" in csv_str
    assert "total_queries" in csv_str


def test_export_markdown(temp_artifact_dir: Path):
    """Test Markdown export."""
    aggregator = MetricsAggregator(temp_artifact_dir)
    metrics = aggregator.get_metrics(days=7)

    md_str = export_markdown(metrics)

    assert "# Clarity Agent Metrics Report" in md_str
    assert "Total Queries" in md_str
    assert "## Daily Breakdown" in md_str


def test_empty_metrics():
    """Test aggregator with no data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact_path = Path(tmpdir)
        artifact_path.joinpath("traces").mkdir()
        artifact_path.joinpath("audit_logs").mkdir()

        aggregator = MetricsAggregator(artifact_path)
        metrics = aggregator.get_metrics(days=7)

        assert metrics.total_queries == 0
        assert metrics.avg_latency_ms == 0


def test_date_filtering():
    """Test that date filtering works."""
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact_path = Path(tmpdir)
        audit_dir = artifact_path / "audit_logs"
        audit_dir.mkdir()

        # Create events from 10 days ago and 2 days ago
        old_date = (datetime.utcnow() - timedelta(days=10)).date().isoformat()
        recent_date = (datetime.utcnow() - timedelta(days=2)).date().isoformat()

        audit_file = audit_dir / "run_old.jsonl"
        with open(audit_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "timestamp": f"{old_date}T10:00:00",
                        "run_id": "run_old",
                        "event_type": "policy_check",
                        "decision": "allowed",
                    }
                )
                + "\n"
            )

        audit_file = audit_dir / "run_recent.jsonl"
        with open(audit_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "timestamp": f"{recent_date}T10:00:00",
                        "run_id": "run_recent",
                        "event_type": "policy_check",
                        "decision": "allowed",
                    }
                )
                + "\n"
            )

        aggregator = MetricsAggregator(artifact_path)

        # Query last 7 days
        metrics_7days = aggregator.get_metrics(days=7)
        assert metrics_7days.total_queries == 1  # Only recent_date

        # Query last 14 days
        metrics_14days = aggregator.get_metrics(days=14)
        assert metrics_14days.total_queries == 2  # Both
