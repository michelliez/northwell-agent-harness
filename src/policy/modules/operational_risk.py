from __future__ import annotations

import re

from policy.normalize import (
    is_schema_metadata_request,
    matches_blocked_term,
    normalize_prompt,
)
from policy.result import PolicyGateResult, blocked

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
    "create table": "Requests an unauthorized database write",
    "create or replace": "Requests an unauthorized database write",
    "execute immediate": "Requests unauthorized dynamic SQL execution",
}

_SCHEMA_IDENTIFIER = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")
_SPACED_SCHEMA_IDENTIFIER = re.compile(
    r"\b[A-Za-z]+\d+[A-Za-z0-9]*\s+"
    r"(?:delete|drop|update|truncate|alter|merge|insert)\b",
    re.IGNORECASE,
)

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


def check_local_execution_and_exfiltration(text: str, q: str) -> PolicyGateResult | None:
    return match_terms(text, q, LOCAL_EXECUTION_AND_EXFILTRATION_TERMS)


def check_scope_expansion(text: str, q: str) -> PolicyGateResult | None:
    for term, reason in SCOPE_EXPANSION_TERMS.items():
        if term == "patients table" and is_schema_metadata_request(q):
            continue
        if matches_blocked_term(text, q, term):
            return blocked(reason=reason, matched_term=term)
    return None


def check_destructive_db(text: str, q: str) -> PolicyGateResult | None:
    # Underscore-delimited catalog identifiers are data, not SQL operations.
    # Normalization changes ``A0H_UPDATE`` into ``a0h update``, which would
    # otherwise look like a destructive verb. Mask only identifiers containing
    # a destructive segment; real commands around the identifier remain visible.
    screened_text = _SCHEMA_IDENTIFIER.sub(_mask_destructive_identifier, text)
    # Users often omit the underscore when naming catalog objects in natural
    # language ("A0H update" for A0H_UPDATE). A code-like prefix followed by a
    # destructive-looking suffix is still an identifier, not an instruction.
    screened_text = _SPACED_SCHEMA_IDENTIFIER.sub(" ", screened_text)
    return match_terms(screened_text, normalize_prompt(screened_text), DESTRUCTIVE_DB_TERMS)


def _mask_destructive_identifier(match: re.Match[str]) -> str:
    identifier = match.group(0)
    segments = {segment.lower() for segment in identifier.split("_")}
    if segments.isdisjoint(DESTRUCTIVE_DB_TERMS):
        return identifier
    return " "


def check_broad_data_exposure(text: str, q: str) -> PolicyGateResult | None:
    return match_terms(text, q, BROAD_DATA_EXPOSURE_TERMS)


def match_terms(text: str, q: str, terms: dict[str, str]) -> PolicyGateResult | None:
    for term, reason in terms.items():
        if matches_blocked_term(text, q, term):
            return blocked(reason=reason, matched_term=term)
    return None
