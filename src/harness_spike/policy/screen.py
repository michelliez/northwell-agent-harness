"""Surface-aware deterministic screening for model-context boundaries.

The user-input policy remains the full policy gate.  Other surfaces use
narrower checks because tool metadata and catalog results are data, not user
authorization requests.  This module deliberately does not implement
identity, authorization, or a complete prompt-injection defense.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from harness_spike.policy.gates import policy_gate
from harness_spike.policy.modules.pii import check_row_level_request
from harness_spike.policy.normalize import matches_blocked_term, normalize_prompt
from harness_spike.policy.result import PolicyGateResult


class ContentSurface(StrEnum):
    """The trust boundary at which content is being screened."""

    USER_INPUT = "user_input"
    TOOL_METADATA = "tool_metadata"
    TOOL_RESULT = "tool_result"
    FINAL_ANSWER = "final_answer"


@dataclass(frozen=True)
class ContentScreenResult:
    """A deterministic screen result that retains the content surface."""

    allowed: bool
    surface: ContentSurface
    reason: str | None = None
    matched_term: str | None = None

    def as_policy_result(self) -> PolicyGateResult:
        """Return the legacy policy result shape for the input trace event."""
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "matched_term": self.matched_term,
        }


class ContentScreenBlocked(RuntimeError):
    """Raised when host execution must stop at a content boundary."""

    def __init__(
        self,
        result: ContentScreenResult,
        location: str,
        used_tools: list[str] | None = None,
    ) -> None:
        self.result = result
        self.location = location
        self.used_tools = list(used_tools or [])
        super().__init__(f"{location}: {result.reason or 'content blocked'}")


MAX_SCREEN_TEXT_LENGTH = 32_000


# These are intentionally narrower than the user-input policy.  A tool
# description can legitimately mention patients, records, or safety labels;
# it must not be able to turn data into instructions for the model.
UNTRUSTED_INSTRUCTION_TERMS: dict[str, str] = {
    "ignore previous instructions": "Untrusted content attempts to override instructions",
    "ignore all previous instructions": "Untrusted content attempts to override instructions",
    "ignore all prior instructions": "Untrusted content attempts to override instructions",
    "disregard the instructions": "Untrusted content attempts to override instructions",
    "ignore all rules": "Untrusted content attempts to override rules",
    "override policy": "Untrusted content attempts to override policy",
    "bypass safety": "Untrusted content attempts to bypass safety",
    "reveal secrets": "Untrusted content requests secret disclosure",
    "print secrets": "Untrusted content requests secret disclosure",
    "exfiltrate": "Untrusted content requests data exfiltration",
    "system message": "Untrusted content impersonates a system message",
    "developer message": "Untrusted content impersonates a developer message",
}


# These patterns target values, not schema field names.  For example,
# ``patient_id`` in a schema description is allowed, while
# ``patient_id: P123`` in a generated answer is blocked.
OUTPUT_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(
            r"\b(?:patient[\s_-]+name|date[\s_-]+of[\s_-]+birth|dob|mrn|ssn|"
            r"phone(?:[\s_-]+number)?|email(?:[\s_-]+address)?|home[\s_-]+address)\s*[:=]",
            re.IGNORECASE,
        ),
        "Final answer contains a direct patient identifier value",
        "sensitive field value",
    ),
)

OUTPUT_ROW_LEVEL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:individual|raw|patient)[\s_-]+(?:records?|rows?)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:records?|rows?)\s*:", re.IGNORECASE),
)


SENSITIVE_RESULT_KEYS = {
    "rows",
    "records",
    "patient_name",
    "patient_names",
    "date_of_birth",
    "dob",
    "ssn",
    "mrn",
    "raw_records",
}


def screen_content(value: Any, surface: ContentSurface) -> ContentScreenResult:
    """Screen content according to the boundary it is crossing.

    User input uses the existing full deterministic policy.  Tool metadata and
    results receive instruction-injection and structural exposure checks.  A
    final answer receives instruction-injection, row-level, and direct-value
    checks.  No branch treats a pass as authorization.
    """
    text = _text(value)
    if len(text) > MAX_SCREEN_TEXT_LENGTH:
        return _blocked(
            surface,
            "Content exceeds the deterministic screen size limit",
            "content-too-large",
        )

    if surface is ContentSurface.USER_INPUT:
        result = policy_gate(text)
        return ContentScreenResult(
            allowed=result["allowed"],
            surface=surface,
            reason=result["reason"],
            matched_term=result["matched_term"],
        )

    instruction_result = _screen_untrusted_instructions(text, surface)
    if not instruction_result.allowed:
        return instruction_result

    if surface is ContentSurface.TOOL_RESULT:
        structural_result = _screen_result_shape(value, surface)
        if not structural_result.allowed:
            return structural_result

    if surface is ContentSurface.FINAL_ANSWER:
        for pattern in OUTPUT_ROW_LEVEL_PATTERNS:
            if pattern.search(text):
                return _blocked(
                    surface,
                    "Final answer contains row-level output",
                    "row-level output",
                )
        row_result = check_row_level_request(normalize_prompt(text))
        if row_result is not None and not row_result["allowed"]:
            return ContentScreenResult(
                allowed=False,
                surface=surface,
                reason=row_result["reason"],
                matched_term=row_result["matched_term"],
            )
        for pattern, reason, matched_term in OUTPUT_VALUE_PATTERNS:
            if pattern.search(text):
                return _blocked(surface, reason, matched_term)

    return ContentScreenResult(allowed=True, surface=surface)


def _screen_untrusted_instructions(text: str, surface: ContentSurface) -> ContentScreenResult:
    normalized = normalize_prompt(text)
    for term, reason in UNTRUSTED_INSTRUCTION_TERMS.items():
        if matches_blocked_term(text, normalized, term):
            return _blocked(surface, reason, term)
    return ContentScreenResult(allowed=True, surface=surface)


def _screen_result_shape(value: Any, surface: ContentSurface) -> ContentScreenResult:
    """Reject obvious row-shaped result payloads before model reuse."""
    if _contains_sensitive_result_key(value):
        return _blocked(
            surface,
            "Tool result contains an unapproved row-level data shape",
            "row-level result shape",
        )
    return ContentScreenResult(allowed=True, surface=surface)


def _contains_sensitive_result_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key in SENSITIVE_RESULT_KEYS:
                return True
            if _contains_sensitive_result_key(item):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive_result_key(item) for item in value)
    return False


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _blocked(
    surface: ContentSurface,
    reason: str,
    matched_term: str,
) -> ContentScreenResult:
    return ContentScreenResult(
        allowed=False,
        surface=surface,
        reason=reason,
        matched_term=matched_term,
    )


__all__ = [
    "ContentScreenBlocked",
    "ContentScreenResult",
    "ContentSurface",
    "MAX_SCREEN_TEXT_LENGTH",
    "screen_content",
]
