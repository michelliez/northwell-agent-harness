"""SQL validation does not authorize data disclosure."""

from __future__ import annotations

from sql.validation import validate_sql


def test_structural_sql_approval_is_not_disclosure_approval(standard_snapshot) -> None:
    result = validate_sql(
        "SELECT COUNT(*) AS appointment_count FROM appointments",
        ["appointments"],
        standard_snapshot,
    )

    assert result.allowed is True
    assert result.disclosure_status == "not_evaluated"
    assert result.requires_authorized_execution is True


def test_blocked_sql_still_reports_disclosure_as_not_evaluated(standard_snapshot) -> None:
    result = validate_sql(
        "SELECT status FROM appointments",
        ["appointments"],
        standard_snapshot,
    )

    assert result.allowed is False
    assert result.disclosure_status == "not_evaluated"
