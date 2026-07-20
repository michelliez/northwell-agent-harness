from policy.consolidate import consolidate_findings
from policy.result import PolicyFinding, blocked


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
