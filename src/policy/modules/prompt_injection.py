from __future__ import annotations

from policy.normalize import matches_blocked_term
from policy.result import PolicyGateResult, blocked

POLICY_MANIPULATION_TERMS: dict[str, str] = {
    "ignore policy": "Tries to change rules",
    "ignore rules": "Tries to change rules",
    "ignore the rules": "Tries to change rules",
    "ignore all rules": "Tries to change rules",
    "ignore permissions": "Tries to change rules",
    "bypass": "Tries to change rules",
    "override": "Tries to change rules",
    "disable safety": "Tries to change rules",
    "forget rules": "Tries to change rules",
    "policy does not apply": "Tries to change rules",
    "pretend you": "Tries to change rules",
    "pretend this is allowed": "Tries to change rules",
    "pretend you are allowed": "Tries to change rules",
    "pretend you're allowed": "Tries to change rules",
    "for testing only": "Tries to override policy",
    "for audit purposes": "Tries to override policy",
    "i am authorized": "Tries to override policy",
}

TOOL_BYPASS_TERMS: dict[str, str] = {
    "previous rules": "Attempts to bypass policy rules",
    "manager approved": "Attempts permission escalation",
    "policy gate already passed": "Attempts to skip a policy node",
    "execution step": "Attempts to bypass validation nodes",
    "safety checks": "Attempts to disable guardrails",
    "not listed": "Attempts to invoke unregistered capabilities",
    "new tool": "Attempts dynamic capability creation",
    "another agent": "Attempts privilege escalation through agent spawning",
    "broader permissions": "Attempts privilege escalation",
    "repeatedly": "Attempts to bypass bounded retry limits",
    "retry forever": "Requests an unbounded execution loop",
    "without logging": "Attempts to bypass audit requirements",
}


def check_policy_manipulation(text: str, q: str) -> PolicyGateResult | None:
    return match_terms(text, q, POLICY_MANIPULATION_TERMS)


def check_tool_bypass(text: str, q: str) -> PolicyGateResult | None:
    return match_terms(text, q, TOOL_BYPASS_TERMS)


def match_terms(text: str, q: str, terms: dict[str, str]) -> PolicyGateResult | None:
    for term, reason in terms.items():
        if matches_blocked_term(text, q, term):
            return blocked(reason=reason, matched_term=term)
    return None
