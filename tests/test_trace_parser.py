from __future__ import annotations

from pathlib import Path

from trace_viewer.parser import load_traces, parse_file

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic_traces"


def test_linear_success():
    traces, warnings = load_traces([FIXTURES / "linear_success.jsonl"])
    assert len(traces) == 1
    t = traces[0]
    assert t.run_id == "aaa"
    assert t.status == "success"
    assert len(t.events) == 12
    assert t.events[0].event_type == "request.received"
    assert t.events[-1].event_type == "answer.ready"
    assert t.total_duration is not None
    assert t.total_duration > 0
    assert len(t.nodes) == 12
    assert len(t.edges) == 11


def test_policy_blocked():
    traces, _ = load_traces([FIXTURES / "policy_blocked.jsonl"])
    assert len(traces) == 1
    t = traces[0]
    assert t.run_id == "bbb"
    assert t.status == "blocked"
    assert len(t.events) == 3
    assert t.events[1].status == "blocked"
    assert t.events[2].event_type == "request.blocked"
    blocked_edge = t.edges[0]
    assert blocked_edge.label == "allowed" or blocked_edge.edge_type == "default"


def test_intent_failure():
    traces, _ = load_traces([FIXTURES / "intent_failure.jsonl"])
    assert len(traces) == 1
    t = traces[0]
    assert t.run_id == "ccc"
    assert t.status == "error"
    assert t.events[-1].event_type == "intent.classification.failed"
    assert t.events[-1].error == "ToolError"


def test_general_question():
    traces, _ = load_traces([FIXTURES / "general_question.jsonl"])
    assert len(traces) == 1
    t = traces[0]
    assert t.status == "success"
    assert any(e.event_type == "general_question.started" for e in t.events)
    assert t.events[-1].event_type == "answer.ready"


def test_malformed_lines():
    runs, warnings = parse_file(FIXTURES / "malformed.jsonl")
    assert len(warnings) >= 2
    assert "eee" in runs
    assert any("malformed JSON" in w for w in warnings)


def test_missing_run_id():
    runs, warnings = parse_file(FIXTURES / "malformed.jsonl")
    assert any("missing run_id" in w for w in warnings)


def test_directory_loading():
    traces, _ = load_traces([FIXTURES])
    assert len(traces) >= 4


def test_multiple_files():
    traces, _ = load_traces(
        [
            FIXTURES / "linear_success.jsonl",
            FIXTURES / "policy_blocked.jsonl",
        ]
    )
    assert len(traces) == 2
    run_ids = {t.run_id for t in traces}
    assert "aaa" in run_ids
    assert "bbb" in run_ids


def test_event_duration_calculated():
    traces, _ = load_traces([FIXTURES / "linear_success.jsonl"])
    t = traces[0]
    for event in t.events[:-1]:
        assert event.duration is not None
        assert event.duration >= 0
    assert t.events[-1].duration is None


def test_node_labels():
    traces, _ = load_traces([FIXTURES / "linear_success.jsonl"])
    t = traces[0]
    assert t.nodes[0].label == "Request Received"
    assert "Allowed" in t.nodes[1].label
    assert t.nodes[-1].label == "Answer Ready"


def test_edge_labels():
    traces, _ = load_traces([FIXTURES / "linear_success.jsonl"])
    t = traces[0]
    assert t.edges[1].label == "allowed"
    assert t.edges[-1].label == "complete"


def test_real_trace():
    real_dir = Path("logs/runs")
    if not real_dir.exists():
        return
    files = list(real_dir.glob("*.jsonl"))[:3]
    if not files:
        return
    traces, warnings = load_traces(files)
    assert len(traces) > 0
    for t in traces:
        assert t.run_id
        assert len(t.events) > 0
        assert len(t.nodes) == len(t.events)
        assert len(t.edges) == len(t.events) - 1
