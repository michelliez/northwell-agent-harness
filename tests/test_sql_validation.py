import pytest

from harness_spike.mcp_servers.sql_validation import validate_sql


@pytest.mark.parametrize(
    ("sql", "tables"),
    [
        (
            "SELECT status, COUNT(*) AS appointment_count "
            "FROM appointments GROUP BY status",
            ["appointments"],
        ),
        (
            "WITH grouped AS ("
            "SELECT status, COUNT(*) AS n FROM appointments GROUP BY status"
            ") SELECT COUNT(*) FROM grouped",
            ["appointments"],
        ),
        (
            "SELECT COUNTIF(status = 'completed') FROM `appointments`",
            ["appointments"],
        ),
        (
            "SELECT e.department, COUNT(DISTINCT p.patient_id) AS patient_count "
            "FROM encounters AS e "
            "JOIN patients AS p ON e.patient_id = p.patient_id "
            "GROUP BY e.department",
            ["patients", "encounters"],
        ),
        (
            "SELECT COUNT(*) FROM appointments "
            "WHERE status = 'update' -- delete is data in a comment",
            ["appointments"],
        ),
    ],
)
def test_valid_aggregate_bigquery_sql_is_allowed(
    sql: str, tables: list[str]
) -> None:
    result = validate_sql(sql, tables)

    assert result["allowed"] is True
    assert result["reason"] is None
    assert result["normalized_sql"]
    assert result["violations"] == []
    assert result["referenced_tables"] == sorted(tables)
    assert result["declared_tables"] == sorted(tables)
    assert result["statement_type"] == "Select"
    assert result["validator_version"] == "sqlglot_ast_v2"
    assert result["sqlglot_version"]


@pytest.mark.parametrize(
    ("sql", "tables", "reason"),
    [
        ("SELECT FROM appointments", ["appointments"], "invalid_sql_syntax"),
        (
            "SELECT COUNT(*) FROM appointments; SELECT COUNT(*) FROM patients",
            ["appointments", "patients"],
            "multiple_statement_sql",
        ),
        ("DROP TABLE appointments", ["appointments"], "non_read_only_sql"),
        (
            "CREATE TEMP FUNCTION f(x INT64) AS (x); SELECT COUNT(*) FROM appointments",
            ["appointments"],
            "multiple_statement_sql",
        ),
        (
            "SELECT COUNT(*) FROM EXTERNAL_QUERY('connection', 'SELECT 1')",
            [],
            "unsafe_sql_function",
        ),
        ("SELECT COUNT(*) FROM evil", ["evil"], "unknown_table"),
        (
            "SELECT COUNT(*) FROM evil",
            ["appointments"],
            "unknown_table",
        ),
        (
            "SELECT COUNT(*) FROM appointments",
            [],
            "declared_table_mismatch",
        ),
        (
            "SELECT COUNT(*) FROM appointments",
            ["patients"],
            "declared_table_mismatch",
        ),
        (
            "SELECT nope, COUNT(*) FROM appointments GROUP BY nope",
            ["appointments"],
            "unknown_or_ambiguous_column",
        ),
        ("SELECT * FROM appointments", ["appointments"], "select_star_sql"),
        ("SELECT a.* FROM appointments AS a", ["appointments"], "select_star_sql"),
        ("SELECT COUNT(*) FROM `events_*`", [], "wildcard_table_scan"),
        (
            "SELECT COUNT(*) FROM `project.dataset.appointments`",
            ["appointments"],
            "qualified_table_not_allowed",
        ),
        ("SELECT status FROM appointments", ["appointments"], "non_aggregate_sql"),
        (
            "SELECT COUNT(*) FROM patients WHERE patient_id = 'example'",
            ["patients"],
            "identifier_column_disallowed_context",
        ),
        (
            "SELECT patient_id, COUNT(*) FROM patients GROUP BY patient_id",
            ["patients"],
            "identifier_column_disallowed_context",
        ),
        (
            "SELECT zip3, COUNT(*) FROM patients GROUP BY zip3",
            ["patients"],
            "sensitive_column_reference",
        ),
    ],
)
def test_unsafe_or_invalid_sql_is_blocked(
    sql: str, tables: list[str], reason: str
) -> None:
    result = validate_sql(sql, tables)

    assert result["allowed"] is False
    assert result["reason"] == reason
    assert result["normalized_sql"] is None
    assert result["violations"][0]["code"] == reason


def test_identifier_count_is_allowed_but_alias_filter_is_not() -> None:
    allowed = validate_sql(
        "SELECT COUNT(DISTINCT p.patient_id) FROM patients AS p",
        ["patients"],
    )
    blocked = validate_sql(
        "SELECT COUNT(*) FROM patients AS p WHERE p.patient_id = 'example'",
        ["patients"],
    )

    assert allowed["allowed"] is True
    assert blocked["reason"] == "identifier_column_disallowed_context"


def test_string_literals_and_comments_do_not_trigger_lexical_false_positives() -> None:
    result = validate_sql(
        "SELECT COUNT(*) FROM appointments "
        "WHERE status IN ('update', 'patient_id') -- drop is not an operation",
        ["appointments"],
    )

    assert result["allowed"] is True
