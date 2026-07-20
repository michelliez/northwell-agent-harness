from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class PolicyGateResult(TypedDict):
    allowed: bool
    reason: str | None
    matched_term: str | None


@dataclass(frozen=True)
class PolicyFinding:
    module: str
    result: PolicyGateResult


def blocked(reason: str, matched_term: str) -> PolicyGateResult:
    return {
        "allowed": False,
        "reason": reason,
        "matched_term": matched_term,
    }


def allowed() -> PolicyGateResult:
    return {
        "allowed": True,
        "reason": None,
        "matched_term": None,
    }


def no_verdict() -> PolicyGateResult:
    """No module fired. Passes to L2 for semantic evaluation.

    Distinct from allowed() so traces can show whether L1 explicitly
    whitelisted a request or simply had no deterministic verdict.
    """
    return {
        "allowed": True,
        "reason": "no_deterministic_verdict",
        "matched_term": None,
    }
