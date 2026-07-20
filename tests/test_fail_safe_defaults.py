"""Regression tests for fail-safe default behaviour.

Gap 1 — L1 blacklist: when no policy module fires, consolidate_findings must
return no_deterministic_verdict (not a silent allowed()) so the audit trail
clearly shows that L2 is the authority for this request.

Gap 2 — L2 failure: when the intent classifier is unavailable or returns
invalid data, uncertain_intent_response must set allowed=False so the
response is correctly marked blocked in traces and by API consumers.
"""

from __future__ import annotations

import pytest

from policy.consolidate import consolidate_findings
from policy.result import PolicyFinding, allowed, blocked

# ---------------------------------------------------------------------------
# Gap 1 — consolidate_findings no-verdict behaviour
# ---------------------------------------------------------------------------


def test_no_findings_returns_no_deterministic_verdict() -> None:
    """Empty findings list must yield no_deterministic_verdict, not silent allow."""
    result = consolidate_findings([])
    assert result["allowed"] is True
    assert result["reason"] == "no_deterministic_verdict"
    assert result["matched_term"] is None


def test_only_block_findings_returns_block() -> None:
    """A blocking finding must be returned immediately."""
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
    """A positive whitelist match (reason=None) must return allowed(), not no_verdict."""
    findings = [
        PolicyFinding(module="workflow_authorization.allowed", result=allowed()),
    ]
    result = consolidate_findings(findings)
    assert result["allowed"] is True
    assert result["reason"] is None  # explicit whitelist, not no_verdict


def test_block_before_allow_returns_block() -> None:
    """A blocking module fires before a whitelist module — block wins."""
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
    """no_verdict must have a non-None reason to distinguish it from an explicit whitelist allow."""
    no_verdict_result = consolidate_findings([])
    explicit_allow_result = consolidate_findings(
        [PolicyFinding(module="workflow_authorization.allowed", result=allowed())]
    )
    assert no_verdict_result["reason"] is not None
    assert explicit_allow_result["reason"] is None


# ---------------------------------------------------------------------------
# Gap 2 — uncertain_intent_response sets allowed=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intent_classifier_failure_returns_allowed_false(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the intent MCP raises, uncertain_intent_response must set allowed=False."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from agent_host.agent import answer_question

    settings = SimpleNamespace(
        trace_dir=str(tmp_path),
        log_raw_prompts=False,
        intent_mcp_url="http://localhost:8002/mcp",
        mcp_server_url="http://localhost:8000/mcp",
        sql_generation_mcp_url="http://localhost:8003/mcp",
        sql_validation_mcp_url="http://localhost:8004/mcp",
        max_tool_rounds=3,
        require_claude_model=lambda: "test-model",
        require_anthropic_api_key=lambda: "test-key",
        require_anthropic_base_url=lambda: "https://example.test",
        anthropic_custom_headers={},
    )
    monkeypatch.setattr("agent_host.agent.get_settings", lambda: settings)

    failing_bridge = AsyncMock()
    failing_bridge.__aenter__ = AsyncMock(return_value=failing_bridge)
    failing_bridge.__aexit__ = AsyncMock(return_value=False)
    failing_bridge.call_tool = AsyncMock(side_effect=ConnectionError("MCP unreachable"))

    with patch("agent_host.agent.MCPToolBridge", return_value=failing_bridge):
        result = await answer_question("How many encounters happened last month?")

    assert result.allowed is False
    assert result.policy_reason == "intent_classifier_uncertain"


@pytest.mark.asyncio
async def test_intent_classifier_invalid_response_returns_allowed_false(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the intent MCP returns non-dict data, uncertain_intent_response must set allowed=False."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from agent_host.agent import answer_question

    settings = SimpleNamespace(
        trace_dir=str(tmp_path),
        log_raw_prompts=False,
        intent_mcp_url="http://localhost:8002/mcp",
        mcp_server_url="http://localhost:8000/mcp",
        sql_generation_mcp_url="http://localhost:8003/mcp",
        sql_validation_mcp_url="http://localhost:8004/mcp",
        max_tool_rounds=3,
        require_claude_model=lambda: "test-model",
        require_anthropic_api_key=lambda: "test-key",
        require_anthropic_base_url=lambda: "https://example.test",
        anthropic_custom_headers={},
    )
    monkeypatch.setattr("agent_host.agent.get_settings", lambda: settings)

    bad_bridge = AsyncMock()
    bad_bridge.__aenter__ = AsyncMock(return_value=bad_bridge)
    bad_bridge.__aexit__ = AsyncMock(return_value=False)
    bad_bridge.call_tool = AsyncMock(return_value="not a dict")

    with patch("agent_host.agent.MCPToolBridge", return_value=bad_bridge):
        result = await answer_question("How many encounters happened last month?")

    assert result.allowed is False
    assert result.policy_reason == "intent_classifier_uncertain"
