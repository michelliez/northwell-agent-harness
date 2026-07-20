from __future__ import annotations

import re
from typing import Any

SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"patient",
        r"ssn",
        r"mrn",
        r"dob",
        r"date.?of.?birth",
        r"address",
        r"phone",
        r"email",
        r"api.?key",
        r"token",
        r"secret",
        r"credential",
        r"password",
        r"authorization",
    ]
]

REDACTED = "[REDACTED]"


def _is_sensitive_key(key: str) -> bool:
    return any(p.search(key) for p in SENSITIVE_PATTERNS)


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: REDACTED if _is_sensitive_key(k) else redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    return obj
