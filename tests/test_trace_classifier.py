from trace_viewer.classifier import classify_event


def test_known_event_types():
    assert classify_event("request.received") == "input"
    assert classify_event("policy_gate.checked") == "policy_gate"
    assert classify_event("request.blocked") == "policy_gate"
    assert classify_event("intent.classification.request") == "classifier"
    assert classify_event("intent.classification.result") == "classifier"
    assert classify_event("intent.classification.failed") == "error"
    assert classify_event("general_question.started") == "classifier"
    assert classify_event("sql.workflow.started") == "generation"
    assert classify_event("sql.workflow.completed") == "generation"
    assert classify_event("sql.generation.refused") == "generation"
    assert classify_event("mcp.tools.listed") == "tool_call"
    assert classify_event("model.request") == "model_call"
    assert classify_event("model.response") == "model_call"
    assert classify_event("tool.selected") == "tool_call"
    assert classify_event("tool.result") == "tool_call"
    assert classify_event("answer.ready") == "output"


def test_legacy_event_types():
    assert classify_event("model.request.first") == "model_call"
    assert classify_event("model.response.first") == "model_call"
    assert classify_event("model.request.final") == "model_call"
    assert classify_event("model.response.final") == "model_call"


def test_unknown_event():
    assert classify_event("some.future.event") == "unknown"
    assert classify_event("") == "unknown"
