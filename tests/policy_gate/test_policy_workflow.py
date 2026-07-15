from harness_spike.policy.gates import collect_findings, policy_gate
from harness_spike.policy.normalize import normalize_prompt


def _modules(prompt: str) -> set[str]:
    return {
        finding.module
        for finding in collect_findings(prompt, normalize_prompt(prompt))
    }


def test_workflow_module_marks_safe_sql_generation_workflow() -> None:
    assert "workflow_authorization.allowed" in _modules("Write SQL to count appointments by status.")


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
