from __future__ import annotations

from harness_spike.mcp_servers.sql_validation import validate_sql


def test_structural_sql_approval_is_not_disclosure_approval() -> None:
    result = validate_sql(
        "SELECT COUNT(*) AS appointment_count FROM appointments",
        ["appointments"],
    )

    assert result["allowed"] is True
    assert result["disclosure_status"] == "not_evaluated"
    assert result["requires_authorized_execution"] is True


def test_blocked_sql_still_reports_disclosure_as_not_evaluated() -> None:
    result = validate_sql(
        "SELECT status FROM appointments",
        ["appointments"],
    )

    assert result["allowed"] is False
    assert result["disclosure_status"] == "not_evaluated"
