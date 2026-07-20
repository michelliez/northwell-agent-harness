from __future__ import annotations

from policy.normalize import matches_blocked_term
from policy.result import PolicyGateResult, blocked

# Persona-switch and fictional-framing attacks not covered by prompt_injection.
# "pretend" variants are already in POLICY_MANIPULATION_TERMS — no duplication here.
JAILBREAK_TERMS: dict[str, str] = {
    "jailbreak": "Explicit jailbreak attempt",
    "developer mode": "Attempts capability unlock via mode framing",
    "roleplay as": "Attempts persona-based jailbreak",
    "you are now a": "Attempts persona switch",
    "your true self": "Attempts persona manipulation",
    "in this roleplay": "Attempts fictional framing bypass",
    "forget everything": "Attempts full instruction reset",
    "ignore all previous": "Attempts to override all prior instructions",
    "act as if you": "Attempts restriction bypass via hypothetical framing",
    "as if you were": "Attempts persona-based restriction bypass",
}


def check_jailbreak(text: str, q: str) -> PolicyGateResult | None:
    for term, reason in JAILBREAK_TERMS.items():
        if matches_blocked_term(text, q, term):
            return blocked(reason=reason, matched_term=term)
    return None
