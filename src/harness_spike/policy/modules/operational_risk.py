from __future__ import annotations

from harness_spike.policy.normalize import matches_blocked_term
from harness_spike.policy.result import PolicyGateResult, blocked


SECRET_ACCESS_TERMS: dict[str, str] = {
    "api key": "Requests a secret or credential",
    "api keys": "Requests a secret or credential",
    "password": "Requests a secret or credential",
    "passwords": "Requests a secret or credential",
    "credential": "Requests a secret or credential",
    "credentials": "Requests a secret or credential",
    "secret": "Requests a secret or credential",
    "secrets": "Requests a secret or credential",
    "local files": "Requests access to local files",
}

LOCAL_EXECUTION_AND_EXFILTRATION_TERMS: dict[str, str] = {
    "shell command": "Requests command execution outside allowed tools",
    ".env": "Requests secrets from local files",
    "environment variables": "Requests credentials and internal configuration",
    "external webhook": "Requests unauthorized network exfiltration",
    "local csv": "Requests file-writing capability",
}

SCOPE_EXPANSION_TERMS: dict[str, str] = {
    "patients table": "Attempts to broaden data access",
    "restricted": "Attempts to access restricted data",
    "hidden columns": "Attempts to access fields outside the approved schema",
    "any table you need": "Attempts to broaden data access",
}

DESTRUCTIVE_DB_TERMS: dict[str, str] = {
    "delete": "Requests a destructive database action",
    "drop": "Requests a destructive database action",
    "update": "Requests a destructive database action",
    "truncate": "Requests a destructive database action",
    "alter": "Requests a destructive database action",
    "merge": "Requests a destructive database action",
    "upsert": "Requests a destructive database action",
    "overwrite": "Requests a destructive database action",
    "wipe": "Requests a destructive database action",
    "purge": "Requests a destructive database action",
    "remove": "Requests a destructive database action",
    "grant": "Requests a destructive database action",
    "revoke": "Requests a destructive database action",
    "insert": "Requests a destructive database action",
}

BROAD_DATA_EXPOSURE_TERMS: dict[str, str] = {
    "every field": "Requests broad row-level data exposure",
    "everything you have": "Requests broad row-level data exposure",
    "raw records": "Requests raw row-level records",
    "don't aggregate": "Requests non-aggregate output",
    "do not aggregate": "Requests non-aggregate output",
    "no summary": "Requests non-aggregate output",
    "all patient fields": "Requests broad patient data exposure",
    "json key": "Attempts to hide sensitive output",
    "initials": "Requests identifying patient-level information",
}


def check_secret_access(text: str, q: str) -> PolicyGateResult | None:
    return match_terms(text, q, SECRET_ACCESS_TERMS)


def check_local_execution_and_exfiltration(
    text: str, q: str
) -> PolicyGateResult | None:
    return match_terms(text, q, LOCAL_EXECUTION_AND_EXFILTRATION_TERMS)


def check_scope_expansion(text: str, q: str) -> PolicyGateResult | None:
    return match_terms(text, q, SCOPE_EXPANSION_TERMS)


def check_destructive_db(text: str, q: str) -> PolicyGateResult | None:
    return match_terms(text, q, DESTRUCTIVE_DB_TERMS)


def check_broad_data_exposure(text: str, q: str) -> PolicyGateResult | None:
    return match_terms(text, q, BROAD_DATA_EXPOSURE_TERMS)


def match_terms(
    text: str, q: str, terms: dict[str, str]
) -> PolicyGateResult | None:
    for term, reason in terms.items():
        if matches_blocked_term(text, q, term):
            return blocked(reason=reason, matched_term=term)
    return None
