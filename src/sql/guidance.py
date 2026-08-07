"""Actionable user guidance for plan- and SQL-validation violation codes.

Violation codes are precise for traces but opaque in a chat answer. This maps
each code the user can act on to one plain sentence; codes that indicate an
internal defect fall back to a generic line rather than leaking mechanics.
"""

from __future__ import annotations

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
        "One column's safety could not be established from documentation, "
        "so it cannot be used; try different columns."
    ),
    "sensitive_column_reference": (
        "That column is sensitive and cannot appear in results or filters."
    ),
    "identifier_column_disallowed_context": (
        "Identifiers may only be counted or used to join tables."
    ),
    "non_aggregate_sql": "Ask for counts, sums, or averages rather than raw rows.",
    "select_star_sql": "Ask for specific aggregate values rather than all columns.",
}

_FALLBACK = "Please rephrase your question."


def describe_violations(codes: list[str]) -> str:
    """One deduplicated guidance sentence per actionable code, else a fallback."""
    seen: list[str] = []
    for code in codes:
        sentence = _GUIDANCE.get(code)
        if sentence and sentence not in seen:
            seen.append(sentence)
    return " ".join(seen) if seen else _FALLBACK
