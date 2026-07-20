from __future__ import annotations

from harness_spike.policy.normalize import contains_phrase
from harness_spike.policy.result import PolicyGateResult, allowed, blocked

SAFE_CATALOG_TERMS = {
    "what table",
    "which table",
    "available tables",
    "schema",
    "column names",
    "columns",
    "fields",
    "describe",
    "what does",
}

SAFE_AGGREGATE_TERMS = {
    "how many",
    "count",
    "average",
    "median",
    "rate",
    "percentage",
    "total",
    "group by",
    "trend",
    "aggregate",
    "summary",
    "compare",
}

SAFE_SQL_TERMS = {
    "sql to count",
    "query to count",
    "select count",
    "write sql to count",
    "generate sql to count",
}

UNSUPPORTED_WORKFLOW_TERMS: dict[str, str] = {
    "send email": "Requests an unsupported workflow",
    "send a message": "Requests an unsupported workflow",
    "schedule a meeting": "Requests an unsupported workflow",
    "create a ticket": "Requests an unsupported workflow",
    "train a model": "Requests an unsupported workflow",
}


def check_allowed_workflow(_text: str, q: str) -> PolicyGateResult | None:
    """Mark prompts that match one of the approved high-level workflows."""
    if any(contains_phrase(q, term) for term in SAFE_CATALOG_TERMS):
        return allowed()
    if any(contains_phrase(q, term) for term in SAFE_AGGREGATE_TERMS):
        return allowed()
    if any(contains_phrase(q, term) for term in SAFE_SQL_TERMS):
        return allowed()
    return None


def check_unsupported_workflow(_text: str, q: str) -> PolicyGateResult | None:
    for term, reason in UNSUPPORTED_WORKFLOW_TERMS.items():
        if contains_phrase(q, term):
            return blocked(reason=reason, matched_term=term)
    return None
