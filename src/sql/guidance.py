"""Actionable user guidance for plan- and SQL-validation violation codes.

Violation codes are precise for traces but opaque in a chat answer. This maps
each code the user can act on to one plain sentence; codes that indicate an
internal defect fall back to a generic line rather than leaking mechanics.
"""

from __future__ import annotations

import collections
from collections.abc import Sequence
from typing import Protocol


class _ViolationLike(Protocol):
    code: str
    evidence: dict


_GUIDANCE: dict[str, str] = {
    # plan authorization
    "objective_mismatch": "Ask the question again in a single plain sentence.",
    "too_many_tables": "Narrow the question to at most four tables.",
    "too_many_joins": "Simplify the question so it needs fewer table joins.",
    "table_out_of_scope": (
        "Name the documented table you want, for example 'in PAT_ENC' — "
        "the plan referenced a table outside the retrieved evidence."
    ),
    "undeclared_plan_table": "Rephrase so every table you mention is part of the question.",
    "column_out_of_scope": (
        "Use column names from the table's documentation — one referenced "
        "column was not found in the retrieved evidence."
    ),
    "row_level_not_allowed": (
        "Ask for counts, sums, or averages — individual records cannot be returned."
    ),
    "unsafe_projection": (
        "Identifiers and sensitive fields cannot be listed in results; "
        "ask for counts or aggregates over them instead."
    ),
    "unsafe_aggregation": "That column is restricted; aggregate a different column.",
    "identifier_usage": "Identifiers can only be counted, not summed or averaged.",
    "unsafe_filter": (
        "Filter on a documented category, status, type, or date column — "
        "the chosen filter column is not classified as safe."
    ),
    "unsafe_join": "Tables can only be joined on their documented identifier columns.",
    "malformed_identifier": (
        "Use plain table and column names exactly as documented — one "
        "referenced name contains unsupported characters."
    ),
    "alias_shadows_restricted_column": (
        "Choose different output names — one result alias matches a restricted column's name."
    ),
    "citation_out_of_scope": "Ask again so the plan can cite the retrieved documentation.",
    "output_shape_mismatch": "Ask again with a simpler description of the desired output.",
    "time_bucket_unsafe_column": (
        "Per-period breakdowns need a documented date or timestamp column "
        "that is safe to aggregate; try a different date column."
    ),
    "time_bucket_not_temporal": (
        "Per-period breakdowns need a DATE, DATETIME, or TIMESTAMP column; "
        "the chosen column is not one."
    ),
    "order_by_unknown_alias": "Order results by one of the requested output values.",
    # sql validation
    "unknown_table": "Name a documented Clarity table; the SQL referenced an unknown one.",
    "unknown_or_ambiguous_column": (
        "Use column names exactly as documented; one column could not be resolved."
    ),
    "unknown_safety_column": (
        "Column {column}'s safety could not be established from documentation; "
        "try a different column."
    ),
    "sensitive_column_reference": (
        "Column {column} is sensitive and cannot appear in results or filters."
    ),
    "identifier_column_disallowed_context": (
        "Column {column} is an identifier; identifiers may only be counted or used to join tables."
    ),
    "non_aggregate_sql": "Ask for counts, sums, or averages rather than raw rows.",
    "select_star_sql": "Ask for specific aggregate values rather than all columns.",
}

_FALLBACK = "Please rephrase your question."


def _safe_evidence(evidence: dict) -> collections.defaultdict:
    return collections.defaultdict(lambda: "unknown", evidence)


def _evidence_detail(value: object) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_evidence_detail(item)}" for key, item in value.items())
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value)
    return str(value)


def describe_violations(violations: Sequence[_ViolationLike]) -> str:
    """One deduplicated guidance sentence per actionable violation, else a fallback.

    Plan-authorization violations carry their specifics under an evidence
    ``value`` key; that detail is appended in parentheses so the user can see
    what the check actually compared, not only how to rephrase.
    """
    seen: list[str] = []
    for v in violations:
        template = _GUIDANCE.get(v.code)
        if not template:
            continue
        sentence = template.format_map(_safe_evidence(v.evidence))
        detail = v.evidence.get("value")
        if detail not in (None, "", [], {}):
            sentence = f"{sentence.rstrip('.')} ({_evidence_detail(detail)})."
        if sentence not in seen:
            seen.append(sentence)
    return " ".join(seen) if seen else _FALLBACK
