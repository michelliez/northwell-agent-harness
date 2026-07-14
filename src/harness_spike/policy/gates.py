from __future__ import annotations

from collections.abc import Callable

from harness_spike.policy.consolidate import consolidate_findings
from harness_spike.policy.modules import (
    operational_risk,
    pii,
    prompt_injection,
    workflow_authorization,
)
from harness_spike.policy.normalize import normalize_prompt
from harness_spike.policy.result import PolicyFinding, PolicyGateResult


PolicyCheck = Callable[[str, str], PolicyGateResult | None]
PolicyCheckEntry = tuple[str, PolicyCheck]


#Ordered from high risk to low risk
POLICY_CHECKS: tuple[PolicyCheckEntry, ...] = (
    ("prompt_injection.policy_manipulation", prompt_injection.check_policy_manipulation),
    ("operational_risk.secret_access", operational_risk.check_secret_access),
    ("prompt_injection.tool_bypass", prompt_injection.check_tool_bypass),
    ("operational_risk.local_execution_and_exfiltration", operational_risk.check_local_execution_and_exfiltration),
    ("workflow_authorization.unsupported", workflow_authorization.check_unsupported_workflow),
    ("operational_risk.scope_expansion", operational_risk.check_scope_expansion),
    ("operational_risk.destructive_db", operational_risk.check_destructive_db),
    ("operational_risk.broad_data_exposure", operational_risk.check_broad_data_exposure),
    ("pii.small_cell_risk", pii.check_small_cell_risk),
    ("pii.known_person_lookup", pii.check_known_person_lookup),
    ("pii.identifiers", pii.check_identifiers),
    ("pii.individual_request", pii.check_individual_request),
    ("pii.indirect_identity", pii.check_indirect_identity),
    ("pii.patient_ranking", pii.check_patient_ranking),
    ("pii.fuzzy_terms", lambda _text, q: pii.check_fuzzy_terms(q)),
    ("pii.row_level_request", lambda _text, q: pii.check_row_level_request(q)),
    ("workflow_authorization.allowed", workflow_authorization.check_allowed_workflow),
)


def policy_gate(question: str) -> PolicyGateResult:
    """Check whether a prompt is allowed before routing to tools or the model."""
    q = normalize_prompt(question)
    return consolidate_findings(collect_findings(question, q))


def collect_findings(question: str, q: str) -> list[PolicyFinding]:
    findings: list[PolicyFinding] = []
    for module, check in POLICY_CHECKS:
        result = check(question, q)
        if result is not None:
            findings.append(
                PolicyFinding(
                    module=module,
                    result=result,
                )
            )
    return findings


__all__ = ["PolicyGateResult", "collect_findings", "policy_gate"]
