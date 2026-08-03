"""Result safety gate: verify returned data doesn't leak sensitive columns."""

from __future__ import annotations

from sql.models import ApprovedQueryPlan


class ResultSafetyViolation(PermissionError):
    """A result contains forbidden columns (sensitive or identifier)."""


def verify_result_safety(
    result_rows: list[dict],
    approved: ApprovedQueryPlan,
) -> list[dict]:
    """Verify result rows don't contain sensitive or identifier columns.

    Args:
        result_rows: Rows returned from BigQuery as list of dicts
        approved: ApprovedQueryPlan that bounded the query

    Returns:
        Same result rows if safety verified

    Raises:
        ResultSafetyViolation: if result contains forbidden columns
    """
    if not result_rows:
        # Empty results are always safe
        return result_rows

    scope = approved.permission_scope
    schema = scope.schema_snapshot

    # Build a mapping of table.column to safety classification
    safety_by_column: dict[str, str] = {}
    for table in schema.tables:
        for column in table.columns:
            key = f"{table.name.lower()}.{column.name.lower()}"
            safety_by_column[key] = column.safety

    # Check all columns in the result
    for row in result_rows:
        for column_name in row:
            # Normalize column name (may be table.column or just column)
            normalized = column_name.lower()

            # Look up safety in two ways:
            # 1. As a fully-qualified table.column
            # 2. As an unqualified column (check all tables)
            safety = safety_by_column.get(normalized)
            if safety is None:
                # Try to find it unqualified by searching all tables
                for table in schema.tables:
                    for col in table.columns:
                        if col.name.lower() == normalized:
                            safety = col.safety
                            break
                    if safety is not None:
                        break
            if safety is None:
                # Column not in schema; check if it's an aggregate or computed
                # Aggregates are safe (they come from the approved plan)
                if any(agg.alias.lower() == normalized for agg in approved.plan.aggregations):
                    continue
                if any(
                    grouping.column.lower() == normalized for grouping in approved.plan.groupings
                ):
                    continue
                # Unknown column in result; this is an error
                raise ResultSafetyViolation(f"Result contains unmapped column: {column_name}")

            # Verify the column is safe to return
            if safety in {"sensitive", "identifier"}:
                raise ResultSafetyViolation(
                    f"Result contains {safety} column that should not be projected: {column_name}"
                )

    return result_rows


def redact_result(
    result_rows: list[dict],
    approved: ApprovedQueryPlan,
) -> list[dict]:
    """Redact sensitive and identifier columns from results.

    This is stricter than verify_result_safety: it removes forbidden columns
    rather than raising an error. Use when the query may have accidentally
    included columns and you want to strip them.

    Args:
        result_rows: Rows returned from BigQuery
        approved: ApprovedQueryPlan that bounded the query

    Returns:
        Results with forbidden columns removed
    """
    if not result_rows:
        return result_rows

    # Redaction is an allowlist built from the approved plan alone: a column
    # survives only if it is an aggregation alias or a grouping key. The schema
    # snapshot is deliberately not consulted, so an unmapped column is dropped
    # rather than classified.
    allowed_columns = set()

    # Aggregation aliases are allowed
    for agg in approved.plan.aggregations:
        allowed_columns.add(agg.alias.lower())

    # Grouping columns are allowed
    for grouping in approved.plan.groupings:
        allowed_columns.add(grouping.column.lower())

    # Filter each row to only allowed columns
    redacted_rows = []
    for row in result_rows:
        redacted_row = {k: v for k, v in row.items() if k.lower() in allowed_columns}
        redacted_rows.append(redacted_row)

    return redacted_rows
