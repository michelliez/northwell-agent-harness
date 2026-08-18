from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_host.model_usage import record_anthropic_usage, usage_from_response
from agent_host.trace_logger import TraceLogger
from metrics.aggregator import MetricsAggregator
from sql.audit_log import AuditLog


def _response(**overrides):
    counts = {
        "input_tokens": 120,
        "output_tokens": 30,
        "cache_creation_input_tokens": 20,
        "cache_read_input_tokens": 10,
        **overrides,
    }
    return SimpleNamespace(usage=SimpleNamespace(**counts))


def test_records_same_anthropic_usage_in_trace_and_audit(tmp_path) -> None:
    trace = TraceLogger(tmp_path / "traces", run_id="run-usage")

    usage = record_anthropic_usage(
        _response(),
        trace=trace,
        artifact_path=tmp_path,
        operation="intent_classification",
        model="claude-test",
    )

    assert usage.total_tokens == 180
    trace_row = json.loads(trace.path.read_text(encoding="utf-8"))
    assert trace_row["event"] == "model.usage"
    assert trace_row["input_tokens"] == 120
    assert trace_row["output_tokens"] == 30
    assert trace_row["total_tokens"] == 180

    [audit_event] = AuditLog(tmp_path / "audit_logs").get_events("run-usage")
    assert audit_event.event_type == "model_usage"
    assert audit_event.operation == "intent_classification"
    assert audit_event.model == "claude-test"
    assert audit_event.total_tokens == 180


def test_metrics_aggregates_all_model_calls_in_one_run(tmp_path) -> None:
    trace = TraceLogger(tmp_path / "traces", run_id="run-total")
    for response in (_response(), _response(input_tokens=10, output_tokens=5,
                                             cache_creation_input_tokens=0,
                                             cache_read_input_tokens=0)):
        record_anthropic_usage(
            response,
            trace=trace,
            artifact_path=tmp_path,
            operation="test",
            model="claude-test",
        )

    events = [event.__dict__ for event in AuditLog(tmp_path / "audit_logs").get_events("run-total")]
    assert MetricsAggregator(tmp_path)._extract_tokens(events) == 195


def test_missing_or_invalid_usage_is_rejected() -> None:
    with pytest.raises(ValueError, match="did not include usage"):
        usage_from_response(SimpleNamespace())
    with pytest.raises(ValueError, match="non-negative integer"):
        usage_from_response(_response(input_tokens=-1))
