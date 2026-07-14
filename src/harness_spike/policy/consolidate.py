from __future__ import annotations

from collections.abc import Iterable

from harness_spike.policy.result import PolicyFinding, PolicyGateResult, allowed


def consolidate_findings(findings: Iterable[PolicyFinding]) -> PolicyGateResult:
    """Return the first blocking module result, otherwise allow."""
    for finding in findings:
        if not finding.result["allowed"]:
            return finding.result
    return allowed()
