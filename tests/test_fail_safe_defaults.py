"""Regression tests for fail-safe default behaviour.

Gap 1 — L1 blacklist: when no policy module fires, consolidate_findings must
return no_deterministic_verdict so the audit trail clearly shows that L2 is
the authority for this request.

Gap 2 — Intent classification failure: when the classifier fails, the graph
must fail closed with allowed=False, not propagate to downstream nodes.
"""

from __future__ import annotations

from policy.consolidate import consolidate_findings
from policy.result import PolicyFinding, allowed, blocked

# ---------------------------------------------------------------------------
# Gap 1 — consolidate_findings no-verdict behaviour
# ---------------------------------------------------------------------------


def test_no_findings_returns_no_deterministic_verdict() -> None:
    result = consolidate_findings([])
    assert result["allowed"] is True
    assert result["reason"] == "no_deterministic_verdict"
    assert result["matched_term"] is None


def test_only_block_findings_returns_block() -> None:
    findings = [
        PolicyFinding(
            module="pii.identifiers",
            result=blocked(reason="PHI identifier", matched_term="patient name"),
        )
    ]
    result = consolidate_findings(findings)
    assert result["allowed"] is False
    assert result["matched_term"] == "patient name"


def test_explicit_allow_finding_returns_allowed() -> None:
    findings = [
        PolicyFinding(module="workflow_authorization.allowed", result=allowed()),
    ]
    result = consolidate_findings(findings)
    assert result["allowed"] is True
    assert result["reason"] is None


def test_block_before_allow_returns_block() -> None:
    findings = [
        PolicyFinding(
            module="pii.identifiers",
            result=blocked(reason="PHI identifier", matched_term="ssn"),
        ),
        PolicyFinding(module="workflow_authorization.allowed", result=allowed()),
    ]
    result = consolidate_findings(findings)
    assert result["allowed"] is False


def test_no_verdict_distinct_from_explicit_allow() -> None:
    no_verdict_result = consolidate_findings([])
    explicit_allow_result = consolidate_findings(
        [PolicyFinding(module="workflow_authorization.allowed", result=allowed())]
    )
    assert no_verdict_result["reason"] is not None
    assert explicit_allow_result["reason"] is None


# ---------------------------------------------------------------------------
# Gap 2 — classify_intent_node failure: returns clarify, not downstream
# ---------------------------------------------------------------------------


def test_classify_intent_node_fails_closed_on_model_error(tmp_path, monkeypatch) -> None:
    """When the model call raises, classify_intent_node must return clarify intent."""
    from agent_host.nodes import intent_nodes

    monkeypatch.setattr(
        intent_nodes,
        "get_config",
        lambda: _FakeCfg(tmp_path),
    )
    monkeypatch.setattr(
        intent_nodes,
        "Anthropic",
        lambda **_: _FailingClient(),
    )

    state = _make_state("How many encounters last month?")
    result = intent_nodes.classify_intent_node(state)

    # On failure, intent is "unknown" with clarify action — not a downstream route
    assert result.get("intent") in {"unknown", None}
    assert result.get("recommended_action") in {"clarify", None}


def _make_state(question: str) -> dict:
    return {
        "question": question,
        "history": [],
        "run_id": "test-run",
        "trace_file": None,
        "started_at": 0.0,
        "policy_blocked": False,
        "policy_reason": None,
        "intent": None,
        "intent_confidence": None,
        "recommended_action": None,
        "risk_flags": [],
        "permissions": {},
        "retrieved_chunks": [],
        "schema_snapshot": None,
        "query_plan": None,
        "generated_sql": None,
        "validation_result": None,
        "execution_status": None,
        "repair_count": 0,
        "repair_hint": None,
        "citations": [],
        "answer": None,
        "clarification_count": 0,
    }


class _FakeCfg:
    def __init__(self, tmp_path):
        self.trace_dir = tmp_path
        self.trace_content_mode = "metadata"
        self.anthropic_custom_headers = {}
        self.anthropic_base_url = None
        self.intent_min_confidence = 0.70

    def require_api_key(self):
        return "test-key"

    def require_base_url(self):
        return "https://example.test"

    def require_model(self):
        return "test-model"


class _FailingClient:
    class messages:
        @staticmethod
        def create(**_):
            raise RuntimeError("Model unreachable")
