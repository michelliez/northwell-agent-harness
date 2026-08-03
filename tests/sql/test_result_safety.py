"""Tests for result safety gate: verify results don't leak sensitive columns."""

from __future__ import annotations

import pytest

from sql.compiler import plan_to_bigquery_sql
from sql.models import (
    PermissionScope,
    QueryPlanAST,
    SchemaColumn,
    SchemaSnapshot,
    SchemaTable,
)
from sql.planning import validate_query_plan
from sql.result_safety import ResultSafetyViolation, redact_result, verify_result_safety

CITATION = "chunk-med-records"
QUESTION = "Count appointments by department"


def _scope(
    *,
    include_sensitive: bool = False,
    include_identifier: bool = False,
) -> PermissionScope:
    """Create a test schema with optional sensitive and identifier columns."""
    columns = [
        SchemaColumn(
            name="DEPT_ID",
            data_type="STRING",
            safety="safe_aggregate",  # Must be safe_aggregate to allow grouping
        ),
        SchemaColumn(
            name="APPT_COUNT",
            data_type="INT64",
            safety="safe_aggregate",
        ),
    ]
    if include_sensitive:
        columns.append(
            SchemaColumn(
                name="PATIENT_SSN",
                data_type="STRING",
                safety="sensitive",
            )
        )
    if include_identifier:
        columns.append(
            SchemaColumn(
                name="INTERNAL_ID",
                data_type="NUMERIC",
                safety="identifier",
            )
        )

    return PermissionScope(
        schema_snapshot=SchemaSnapshot(
            tables=[
                SchemaTable(
                    name="APPOINTMENT",
                    columns=columns,
                    source_chunk_ids=[CITATION],
                )
            ],
            derived_from_chunks=[CITATION],
        )
    )


def _approved(
    scope: PermissionScope | None = None,
    aggregation_column: str = "APPT_COUNT",
    grouping_column: str = "DEPT_ID",
) -> object:
    """Create an approved plan with specified columns."""
    if scope is None:
        scope = _scope()

    payload = {
        "objective": QUESTION,
        "target_metric": "appointment_count",
        "tables": ["APPOINTMENT"],
        "aggregations": [
            {
                "function": "SUM",
                "column": {"table": "APPOINTMENT", "column": aggregation_column},
                "alias": "total_count",
            }
        ],
        "groupings": [{"table": "APPOINTMENT", "column": grouping_column}],
        "expected_output": [grouping_column, "total_count"],
        "citations": [CITATION],
    }
    plan = QueryPlanAST.model_validate(payload)
    result = validate_query_plan(QUESTION, plan, scope, {CITATION})
    assert result.approved_plan is not None
    return result.approved_plan


# ── Basic Safety Verification Tests ────────────────────────────────────────


def test_verify_allows_approved_columns() -> None:
    """Test that result contains only approved columns."""
    approved = _approved()
    result = [
        {"DEPT_ID": "CARDIOLOGY", "total_count": 42},
        {"DEPT_ID": "ONCOLOGY", "total_count": 15},
    ]

    # Should not raise
    returned = verify_result_safety(result, approved)
    assert returned == result


def test_verify_rejects_sensitive_column_in_result() -> None:
    """Test that sensitive columns in result are rejected."""
    scope = _scope(include_sensitive=True)
    approved = _approved(scope)

    result = [{"DEPT_ID": "CARDIOLOGY", "total_count": 42, "PATIENT_SSN": "123-45-6789"}]

    with pytest.raises(ResultSafetyViolation, match="sensitive"):
        verify_result_safety(result, approved)


def test_verify_rejects_identifier_column_in_result() -> None:
    """Test that identifier columns in result are rejected."""
    scope = _scope(include_identifier=True)
    approved = _approved(scope)

    result = [{"DEPT_ID": "CARDIOLOGY", "total_count": 42, "INTERNAL_ID": 12345}]

    with pytest.raises(ResultSafetyViolation, match="identifier"):
        verify_result_safety(result, approved)


def test_verify_allows_empty_result() -> None:
    """Test that empty results are always safe."""
    approved = _approved()
    result = []

    # Should not raise
    returned = verify_result_safety(result, approved)
    assert returned == []


# ── Edge Cases ─────────────────────────────────────────────────────────────


def test_verify_case_insensitive_column_matching() -> None:
    """Test that column names are matched case-insensitively."""
    approved = _approved()

    # Result with mixed case column names
    result = [
        {"dept_id": "CARDIOLOGY", "total_count": 42},
        {"DEPT_ID": "ONCOLOGY", "Total_Count": 15},
    ]

    # Should not raise
    returned = verify_result_safety(result, approved)
    assert len(returned) == 2


def test_verify_rejects_unmapped_column() -> None:
    """Test that unknown columns in result are rejected."""
    approved = _approved()

    result = [{"DEPT_ID": "CARDIOLOGY", "total_count": 42, "UNKNOWN_COL": "value"}]

    with pytest.raises(ResultSafetyViolation, match="unmapped"):
        verify_result_safety(result, approved)


def test_verify_handles_null_values() -> None:
    """Test that null values in safe columns are allowed."""
    approved = _approved()

    result = [
        {"DEPT_ID": None, "total_count": 0},
        {"DEPT_ID": "CARDIOLOGY", "total_count": None},
    ]

    # Should not raise
    returned = verify_result_safety(result, approved)
    assert len(returned) == 2


def test_verify_handles_numeric_and_string_types() -> None:
    """Test that different data types are handled correctly."""
    approved = _approved()

    result = [
        {"DEPT_ID": "CARDIOLOGY", "total_count": 42},
        {"DEPT_ID": "ONCOLOGY", "total_count": 15.5},
        {"DEPT_ID": "PEDIATRICS", "total_count": "100"},  # Type coercion
    ]

    # Should not raise
    returned = verify_result_safety(result, approved)
    assert len(returned) == 3


# ── Redaction Tests ───────────────────────────────────────────────────────


def test_redact_removes_sensitive_columns() -> None:
    """Test that redaction removes sensitive columns from results."""
    scope = _scope(include_sensitive=True)
    approved = _approved(scope)

    result = [
        {
            "DEPT_ID": "CARDIOLOGY",
            "total_count": 42,
            "PATIENT_SSN": "123-45-6789",
        }
    ]

    redacted = redact_result(result, approved)

    # SSN should be removed
    assert redacted == [{"DEPT_ID": "CARDIOLOGY", "total_count": 42}]


def test_redact_removes_identifier_columns() -> None:
    """Test that redaction removes identifier columns from results."""
    scope = _scope(include_identifier=True)
    approved = _approved(scope)

    result = [
        {
            "DEPT_ID": "CARDIOLOGY",
            "total_count": 42,
            "INTERNAL_ID": 12345,
        }
    ]

    redacted = redact_result(result, approved)

    # INTERNAL_ID should be removed (DEPT_ID is identifier but also in grouping)
    assert redacted == [{"DEPT_ID": "CARDIOLOGY", "total_count": 42}]


def test_redact_keeps_only_approved_columns() -> None:
    """Test that redaction keeps only explicitly approved columns."""
    scope = _scope(include_sensitive=True, include_identifier=True)
    approved = _approved(scope)

    result = [
        {
            "DEPT_ID": "CARDIOLOGY",
            "total_count": 42,
            "PATIENT_SSN": "123-45-6789",
            "INTERNAL_ID": 12345,
            "EXTRA_COLUMN": "should be removed",
        }
    ]

    redacted = redact_result(result, approved)

    # Only grouping (DEPT_ID) and aggregation (total_count) should remain
    assert redacted == [{"DEPT_ID": "CARDIOLOGY", "total_count": 42}]


def test_redact_preserves_empty_result() -> None:
    """Test that redaction doesn't change empty results."""
    approved = _approved()

    result = []

    redacted = redact_result(result, approved)
    assert redacted == []


def test_redact_case_insensitive_matching() -> None:
    """Test that redaction matches columns case-insensitively."""
    approved = _approved()

    result = [
        {
            "dept_id": "CARDIOLOGY",
            "TOTAL_COUNT": 42,
            "extra": "removed",
        }
    ]

    redacted = redact_result(result, approved)

    # Should match columns case-insensitively
    assert len(redacted[0]) == 2
    assert "extra" not in redacted[0]


# ── Integration Tests ──────────────────────────────────────────────────────


def test_result_safety_with_compiled_query() -> None:
    """Test safety verification with actual compiled query."""
    approved = _approved()
    compiled = plan_to_bigquery_sql(approved)

    # The rows below are hand-written to match the plan, so assert the compiler
    # actually projects those columns -- otherwise this test would keep passing
    # after a compiler change that stopped emitting them.
    assert "DEPT_ID" in compiled.sql
    assert "total_count" in compiled.sql

    # Simulate safe results matching the compiled query
    result = [
        {"DEPT_ID": "CARDIOLOGY", "total_count": 42},
        {"DEPT_ID": "ONCOLOGY", "total_count": 15},
    ]

    # Should verify without error
    returned = verify_result_safety(result, approved)
    assert len(returned) == 2


def test_safety_gate_in_execution_flow() -> None:
    """Test result safety as part of execution flow."""
    approved = _approved()

    # Simulate what the executor would return
    execution_result = [
        {"DEPT_ID": "CARDIOLOGY", "total_count": 42},
        {"DEPT_ID": "ONCOLOGY", "total_count": 15},
    ]

    # Verify safety
    safe_result = verify_result_safety(execution_result, approved)

    # Return to user
    assert safe_result == execution_result


def test_redaction_as_safety_fallback() -> None:
    """Test redaction as a fallback when results may have extra columns."""
    approved = _approved()

    # BigQuery might return extra columns (like job info, etc.)
    result = [
        {
            "DEPT_ID": "CARDIOLOGY",
            "total_count": 42,
            "_job_id": "job-123",
            "_timestamp": "2026-07-30T12:00:00Z",
        }
    ]

    # Use redaction to clean up
    redacted = redact_result(result, approved)

    # Only approved columns remain
    assert redacted == [{"DEPT_ID": "CARDIOLOGY", "total_count": 42}]


# ── Multiple Columns Tests ────────────────────────────────────────────────


def test_verify_multiple_sensitive_columns() -> None:
    """Test rejection when multiple sensitive columns are present."""
    scope = PermissionScope(
        schema_snapshot=SchemaSnapshot(
            tables=[
                SchemaTable(
                    name="APPOINTMENT",
                    columns=[
                        SchemaColumn(
                            name="DEPT_ID",
                            data_type="STRING",
                            safety="safe_aggregate",
                        ),
                        SchemaColumn(
                            name="SSN",
                            data_type="STRING",
                            safety="sensitive",
                        ),
                        SchemaColumn(
                            name="PHONE",
                            data_type="STRING",
                            safety="sensitive",
                        ),
                        SchemaColumn(
                            name="APPT_COUNT",
                            data_type="INT64",
                            safety="safe_aggregate",
                        ),
                    ],
                    source_chunk_ids=[CITATION],
                )
            ],
            derived_from_chunks=[CITATION],
        )
    )

    approved = _approved(scope, aggregation_column="APPT_COUNT", grouping_column="DEPT_ID")

    result = [
        {
            "DEPT_ID": "CARDIOLOGY",
            "total_count": 42,
            "SSN": "123-45-6789",
            "PHONE": "555-1234",
        }
    ]

    # Should reject on first sensitive column
    with pytest.raises(ResultSafetyViolation, match="sensitive"):
        verify_result_safety(result, approved)


def test_redact_multiple_sensitive_columns() -> None:
    """Test redaction removes multiple sensitive columns."""
    scope = PermissionScope(
        schema_snapshot=SchemaSnapshot(
            tables=[
                SchemaTable(
                    name="APPOINTMENT",
                    columns=[
                        SchemaColumn(
                            name="DEPT_ID",
                            data_type="STRING",
                            safety="safe_aggregate",
                        ),
                        SchemaColumn(
                            name="SSN",
                            data_type="STRING",
                            safety="sensitive",
                        ),
                        SchemaColumn(
                            name="PHONE",
                            data_type="STRING",
                            safety="sensitive",
                        ),
                        SchemaColumn(
                            name="APPT_COUNT",
                            data_type="INT64",
                            safety="safe_aggregate",
                        ),
                    ],
                    source_chunk_ids=[CITATION],
                )
            ],
            derived_from_chunks=[CITATION],
        )
    )

    approved = _approved(scope, aggregation_column="APPT_COUNT", grouping_column="DEPT_ID")

    result = [
        {"DEPT_ID": "CARDIOLOGY", "total_count": 42, "SSN": "123-45-6789", "PHONE": "555-1234"}
    ]

    redacted = redact_result(result, approved)

    # Only DEPT_ID (grouping) and total_count (aggregation) should remain
    assert redacted == [{"DEPT_ID": "CARDIOLOGY", "total_count": 42}]


# ── Schema Mismatch Tests ──────────────────────────────────────────────────


def test_verify_handles_aliased_aggregations() -> None:
    """Test that aggregation aliases are recognized as safe."""
    approved = _approved()

    # Result uses the aggregation alias
    result = [{"DEPT_ID": "CARDIOLOGY", "total_count": 42}]

    # Should not raise
    returned = verify_result_safety(result, approved)
    assert returned == result
