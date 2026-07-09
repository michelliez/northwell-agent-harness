from __future__ import annotations

from typing import TypedDict


class PolicyGateResult(TypedDict):
    allowed: bool
    reason: str | None
    matched_term: str | None


BLOCKED_TERMS: dict[str, str] = {
    "patient name": "Requests patient-identifying information",
    "names": "Requests patient-identifying information",
    "mrn": "Requests medical record numbers",
    "dob": "Requests patient-identifying information",
    "date of birth": "Requests patient-identifying information",
    "address": "Requests patient-identifying information",
    "phone number": "Requests patient-identifying information",
    "family members": "Requests patient-identifying information",
    "relatives": "Requests patient-identifying information",
    "which patient": "Asks for individual patient information",
    "who was": "Asks for individual patient information",
    "delete": "Requests a destructive database action",
    "drop": "Requests a destructive database action",
    "update": "Requests a destructive database action",
    "insert": "Requests a destructive database action",
    "ignore policy": "Tries to change rules",
    "ignore permissions": "Tries to change rules",
}


def policy_gate(question: str) -> PolicyGateResult:
    """Check whether a prompt is allowed before routing to tools or the model."""
    q = question.lower()
    for term, reason in BLOCKED_TERMS.items():
        if term in q:
            return {
                "allowed": False,
                "reason": reason,
                "matched_term": term,
            }
    return {
        "allowed": True,
        "reason": None,
        "matched_term": None,
    }
