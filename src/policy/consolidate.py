from __future__ import annotations

from collections.abc import Iterable

from policy.result import PolicyFinding, PolicyGateResult, no_verdict


def consolidate_findings(findings: Iterable[PolicyFinding]) -> PolicyGateResult:
    """Return the first blocking result; the first explicit allow; or no_verdict.

    Priority order:
      1. Any blocking module fires → return that block immediately.
      2. A positive-whitelist module fires → return allowed().
      3. Nothing fired → return no_verdict() so callers know L1 had no opinion
         and must escalate to L2 for a semantic decision.
    """
    explicit_allow: PolicyGateResult | None = None
    for finding in findings:
        if not finding.result["allowed"]:
            return finding.result
        if finding.result["reason"] is None:
            explicit_allow = finding.result
    return explicit_allow if explicit_allow is not None else no_verdict()
