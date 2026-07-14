from __future__ import annotations

from harness_spike.policy.modules.prompt_injection import POLICY_MANIPULATION_TERMS
from harness_spike.policy.normalize import matches_blocked_term
from harness_spike.policy.result import PolicyGateResult, blocked


JAILBREAK_TERMS: dict[str, str] = {
    term: reason
    for term, reason in POLICY_MANIPULATION_TERMS.items()
    if term.startswith("pretend")
}


def check_jailbreak(text: str, q: str) -> PolicyGateResult | None:
    for term, reason in JAILBREAK_TERMS.items():
        if matches_blocked_term(text, q, term):
            return blocked(reason=reason, matched_term=term)
    return None
