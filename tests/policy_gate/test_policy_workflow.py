import pytest

from policy.consolidate import consolidate_findings
from policy.gates import collect_findings, policy_gate
from policy.normalize import normalize_prompt
from policy.result import PolicyFinding, allowed, blocked


def _modules(prompt: str) -> set[str]:
    return {finding.module for finding in collect_findings(prompt, normalize_prompt(prompt))}


def test_workflow_module_marks_safe_sql_generation_workflow() -> None:
    assert "workflow_authorization.allowed" in _modules(
        "Write SQL to count appointments by status."
    )


def test_workflow_module_does_not_allow_generic_sql_generation() -> None:
    assert "workflow_authorization.allowed" not in _modules("Write SQL for appointments.")


def test_workflow_module_marks_safe_catalog_workflow() -> None:
    assert "workflow_authorization.allowed" in _modules("What columns are in encounters?")


def test_workflow_module_blocks_clearly_unsupported_workflow() -> None:
    result = policy_gate("Schedule a meeting with the database admin.")

    assert result == {
        "allowed": False,
        "reason": "Requests an unsupported workflow",
        "matched_term": "schedule a meeting",
    }


def test_safety_findings_win_over_allowed_workflow() -> None:
    result = policy_gate("Write SQL to count appointments by status and delete rows.")

    assert result == {
        "allowed": False,
        "reason": "Requests a destructive database action",
        "matched_term": "delete",
    }


def test_aggregate_plus_individual_records_is_blocked() -> None:
    result = policy_gate("Count visits and list the individual records")

    assert result == {
        "allowed": False,
        "reason": "Requests row-level patient or encounter data",
        "matched_term": "row-level request",
    }


@pytest.mark.parametrize(
    "prompt",
    [
        "What columns are in the patients table?",
        "Describe the patients table schema.",
        "Show the schema for the patients table.",
    ],
)
def test_patient_schema_metadata_is_allowed(prompt: str) -> None:
    assert policy_gate(prompt)["allowed"] is True


@pytest.mark.parametrize(
    "prompt",
    [
        "Show patient names from the patients table.",
        "Use the patients table if that makes the answer easier.",
        "Show hidden columns in the patients table.",
    ],
)
def test_patient_data_access_remains_blocked(prompt: str) -> None:
    assert policy_gate(prompt)["allowed"] is False


def test_consolidation_returns_first_blocking_finding() -> None:
    result = consolidate_findings(
        [
            PolicyFinding(
                module="operational_risk.destructive_db",
                result=blocked("Requests a destructive database action", "delete"),
            ),
            PolicyFinding(
                module="pii.identifiers",
                result=blocked("Requests patient identifiers", "patient id"),
            ),
        ]
    )

    assert result == {
        "allowed": False,
        "reason": "Requests a destructive database action",
        "matched_term": "delete",
    }


def test_no_findings_returns_no_deterministic_verdict() -> None:
    result = consolidate_findings([])
    assert result["allowed"] is True
    assert result["reason"] == "no_deterministic_verdict"
    assert result["matched_term"] is None


def test_only_block_findings_returns_block() -> None:
    result = consolidate_findings(
        [
            PolicyFinding(
                module="pii.identifiers",
                result=blocked(reason="PHI identifier", matched_term="patient name"),
            )
        ]
    )
    assert result["allowed"] is False
    assert result["matched_term"] == "patient name"


def test_explicit_allow_finding_returns_allowed() -> None:
    result = consolidate_findings(
        [PolicyFinding(module="workflow_authorization.allowed", result=allowed())]
    )
    assert result["allowed"] is True
    assert result["reason"] is None


def test_block_before_allow_returns_block() -> None:
    result = consolidate_findings(
        [
            PolicyFinding(
                module="pii.identifiers",
                result=blocked(reason="PHI identifier", matched_term="ssn"),
            ),
            PolicyFinding(module="workflow_authorization.allowed", result=allowed()),
        ]
    )
    assert result["allowed"] is False


def test_no_verdict_distinct_from_explicit_allow() -> None:
    no_verdict_result = consolidate_findings([])
    explicit_allow_result = consolidate_findings(
        [PolicyFinding(module="workflow_authorization.allowed", result=allowed())]
    )
    assert no_verdict_result["reason"] is not None
    assert explicit_allow_result["reason"] is None
