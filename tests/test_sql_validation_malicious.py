import pytest
from pydantic import ValidationError

from harness_spike.mcp_servers.sql_validation import validate_sql


MALICIOUS_SQL_CASES = [
    # DDL, DML, scripting, permissions, and export operations.
    pytest.param(
        "INSERT INTO appointments(status) VALUES ('completed')",
        ["appointments"],
        None,
        id="insert",
    ),
    pytest.param(
        "UPDATE appointments SET status = 'completed' WHERE TRUE",
        ["appointments"],
        None,
        id="update",
    ),
    pytest.param(
        "DELETE FROM appointments WHERE TRUE",
        ["appointments"],
        None,
        id="delete",
    ),
    pytest.param(
        "MERGE appointments AS target USING appointments AS source ON FALSE "
        "WHEN NOT MATCHED THEN INSERT(status) VALUES('completed')",
        ["appointments"],
        None,
        id="merge",
    ),
    pytest.param(
        "CREATE TABLE copied AS SELECT * FROM appointments",
        ["appointments"],
        None,
        id="create_table_as_select",
    ),
    pytest.param(
        "CREATE OR REPLACE VIEW copied AS SELECT * FROM appointments",
        ["appointments"],
        None,
        id="create_view",
    ),
    pytest.param(
        "CREATE TEMP FUNCTION steal(x STRING) AS (x)",
        [],
        None,
        id="create_function",
    ),
    pytest.param(
        "ALTER TABLE appointments ADD COLUMN secret STRING",
        ["appointments"],
        None,
        id="alter_table",
    ),
    pytest.param(
        "DROP TABLE appointments",
        ["appointments"],
        None,
        id="drop_table",
    ),
    pytest.param(
        "TRUNCATE TABLE appointments",
        ["appointments"],
        None,
        id="truncate_table",
    ),
    pytest.param(
        "EXPORT DATA OPTIONS(uri='gs://unapproved/export-*', format='CSV') "
        "AS SELECT * FROM appointments",
        ["appointments"],
        None,
        id="export_data",
    ),
    pytest.param(
        "LOAD DATA INTO appointments FROM FILES(" 
        "format='CSV', uris=['gs://unapproved/input.csv'])",
        ["appointments"],
        None,
        id="load_data",
    ),
    pytest.param("CALL dataset.procedure()", [], None, id="call_procedure"),
    pytest.param(
        "EXECUTE IMMEDIATE 'DELETE FROM appointments WHERE TRUE'",
        [],
        None,
        id="execute_immediate",
    ),
    pytest.param("DECLARE target STRING DEFAULT 'x'", [], None, id="declare"),
    pytest.param("SET target = 'x'", [], None, id="set_variable"),
    pytest.param("BEGIN TRANSACTION", [], None, id="begin_transaction"),
    pytest.param("COMMIT TRANSACTION", [], None, id="commit_transaction"),
    pytest.param("ROLLBACK TRANSACTION", [], None, id="rollback_transaction"),
    pytest.param(
        "GRANT SELECT ON TABLE appointments TO 'user:attacker@example.com'",
        ["appointments"],
        None,
        id="grant",
    ),
    pytest.param(
        "REVOKE SELECT ON TABLE appointments FROM 'user:analyst@example.com'",
        ["appointments"],
        None,
        id="revoke",
    ),
    # Statement stacking and parser smuggling.
    pytest.param(
        "SELECT COUNT(*) FROM appointments; DELETE FROM appointments WHERE TRUE",
        ["appointments"],
        "multiple_statement_sql",
        id="stacked_delete",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM appointments; UPDATE appointments SET status = 'x'",
        ["appointments"],
        "multiple_statement_sql",
        id="stacked_update",
    ),
    pytest.param(
        "DROP TABLE appointments; SELECT COUNT(*) FROM appointments",
        ["appointments"],
        "multiple_statement_sql",
        id="destructive_first_statement",
    ),
    pytest.param(
        "CREATE TEMP FUNCTION f(x INT64) AS (x); "
        "SELECT COUNT(*) FROM appointments",
        ["appointments"],
        "multiple_statement_sql",
        id="function_then_select",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM appointments -- end query\n; DROP TABLE appointments",
        ["appointments"],
        "multiple_statement_sql",
        id="comment_newline_smuggling",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM appointments /* close */; "
        "EXPORT DATA OPTIONS(uri='gs://x/*', format='CSV') "
        "AS SELECT * FROM appointments",
        ["appointments"],
        "multiple_statement_sql",
        id="comment_export_smuggling",
    ),
    # Unapproved, external, generated, or tableless data sources.
    pytest.param("SELECT COUNT(*)", [], "missing_approved_table", id="tableless"),
    pytest.param(
        "SELECT COUNT(*) FROM (SELECT 1 AS value)",
        [],
        "missing_approved_table",
        id="literal_subquery_source",
    ),
    pytest.param(
        "WITH fabricated AS (SELECT 1 AS value) SELECT COUNT(*) FROM fabricated",
        [],
        "missing_approved_table",
        id="literal_cte_source",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM UNNEST(GENERATE_ARRAY(1, 1000000000))",
        [],
        "unapproved_table_source",
        id="generated_array_source",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM appointments "
        "CROSS JOIN UNNEST(GENERATE_ARRAY(1, 1000000000))",
        ["appointments"],
        "unapproved_table_source",
        id="approved_table_cross_generated_array",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM EXTERNAL_QUERY('connection', 'SELECT * FROM secret')",
        [],
        "unsafe_sql_function",
        id="external_query",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM evil",
        ["appointments"],
        "unknown_table",
        id="declared_table_spoof",
    ),
    pytest.param(
        "WITH appointments AS (SELECT status FROM evil) "
        "SELECT COUNT(*) FROM appointments",
        ["appointments"],
        "unknown_table",
        id="cte_name_spoof",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM `project.dataset.appointments`",
        ["appointments"],
        "qualified_table_not_allowed",
        id="fully_qualified_approved_name",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM `region-us`.INFORMATION_SCHEMA.JOBS",
        [],
        "qualified_table_not_allowed",
        id="information_schema_jobs",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM `events_*`",
        [],
        "wildcard_table_scan",
        id="wildcard_table",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM `appointments$20260101`",
        ["appointments"],
        "unknown_table",
        id="partition_decorator",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM `appointments@-3600000`",
        ["appointments"],
        "unknown_table",
        id="time_decorator",
    ),
    # Row-level output hidden behind aggregate syntax.
    pytest.param(
        "SELECT status, COUNT(*) OVER () FROM appointments",
        ["appointments"],
        "window_function_not_allowed",
        id="window_count_row_leak",
    ),
    pytest.param(
        "SELECT status, ROW_NUMBER() OVER (ORDER BY appointment_date), COUNT(*) OVER () "
        "FROM appointments",
        ["appointments"],
        "window_function_not_allowed",
        id="row_number_leak",
    ),
    pytest.param(
        "SELECT status, (SELECT COUNT(*) FROM patients) AS nested_count "
        "FROM appointments",
        ["appointments", "patients"],
        "non_aggregate_sql",
        id="aggregate_only_in_scalar_subquery",
    ),
    pytest.param(
        "SELECT status, COUNT(*) FROM appointments",
        ["appointments"],
        "ungrouped_projection",
        id="ungrouped_row_column",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM appointments UNION ALL "
        "SELECT status FROM appointments",
        ["appointments"],
        "non_aggregate_sql",
        id="union_row_branch",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM appointments EXCEPT DISTINCT "
        "SELECT status FROM appointments",
        ["appointments"],
        "non_aggregate_sql",
        id="except_row_branch",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM appointments INTERSECT DISTINCT "
        "SELECT status FROM appointments",
        ["appointments"],
        "non_aggregate_sql",
        id="intersect_row_branch",
    ),
    pytest.param(
        "SELECT status FROM appointments LIMIT 1",
        ["appointments"],
        "non_aggregate_sql",
        id="limit_does_not_make_rows_safe",
    ),
    # Identifier and sensitive-column context bypasses.
    pytest.param(
        "SELECT patient_id, COUNT(*) FROM patients GROUP BY patient_id",
        ["patients"],
        "identifier_column_disallowed_context",
        id="identifier_projection",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM patients WHERE patient_id = 'target'",
        ["patients"],
        "identifier_column_disallowed_context",
        id="identifier_equality_probe",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM patients WHERE patient_id IN ('a', 'b')",
        ["patients"],
        "identifier_column_disallowed_context",
        id="identifier_in_probe",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM patients WHERE patient_id LIKE 'prefix%'",
        ["patients"],
        "identifier_column_disallowed_context",
        id="identifier_like_probe",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM patients WHERE patient_id IS NULL",
        ["patients"],
        "identifier_column_disallowed_context",
        id="identifier_null_probe",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM patients WHERE patient_id BETWEEN 'a' AND 'z'",
        ["patients"],
        "identifier_column_disallowed_context",
        id="identifier_range_probe",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM patients ORDER BY patient_id",
        ["patients"],
        "identifier_column_disallowed_context",
        id="identifier_ordering",
    ),
    pytest.param(
        "SELECT COUNT(IF(patient_id = 'target', 1, NULL)) FROM patients",
        ["patients"],
        "identifier_column_disallowed_context",
        id="identifier_membership_hidden_in_count",
    ),
    pytest.param(
        "SELECT COUNT(DISTINCT IF(patient_id = 'target', patient_id, NULL)) "
        "FROM patients",
        ["patients"],
        "identifier_column_disallowed_context",
        id="identifier_membership_hidden_in_distinct_count",
    ),
    pytest.param(
        "SELECT COUNT(CONCAT(patient_id, '-suffix')) FROM patients",
        ["patients"],
        "identifier_column_disallowed_context",
        id="identifier_transformation_hidden_in_count",
    ),
    pytest.param(
        "SELECT COUNT(CAST(patient_id AS STRING)) FROM patients",
        ["patients"],
        "identifier_column_disallowed_context",
        id="identifier_cast_hidden_in_count",
    ),
    pytest.param(
        "SELECT APPROX_COUNT_DISTINCT(patient_id) FROM patients",
        ["patients"],
        "identifier_column_disallowed_context",
        id="unapproved_identifier_aggregate",
    ),
    pytest.param(
        "SELECT ARRAY_AGG(patient_id) FROM patients",
        ["patients"],
        "identifier_column_disallowed_context",
        id="identifier_array_aggregation",
    ),
    pytest.param(
        "SELECT STRING_AGG(patient_id) FROM patients",
        ["patients"],
        "identifier_column_disallowed_context",
        id="identifier_string_aggregation",
    ),
    pytest.param(
        "SELECT MAX(patient_id) FROM patients",
        ["patients"],
        "identifier_column_disallowed_context",
        id="identifier_maximum",
    ),
    pytest.param(
        "SELECT zip3, COUNT(*) FROM patients GROUP BY zip3",
        ["patients"],
        "sensitive_column_reference",
        id="sensitive_grouping",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM patients WHERE zip3 = '100'",
        ["patients"],
        "sensitive_column_reference",
        id="sensitive_filtering",
    ),
    pytest.param(
        "SELECT COUNT(zip3) FROM patients",
        ["patients"],
        "sensitive_column_reference",
        id="sensitive_counting",
    ),
    # Cartesian, implicit, non-equality, and probe-bearing joins.
    pytest.param(
        "SELECT COUNT(*) FROM encounters NATURAL JOIN patients",
        ["encounters", "patients"],
        "unsafe_join",
        id="natural_join",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM encounters CROSS JOIN patients",
        ["encounters", "patients"],
        "unsafe_join",
        id="cross_join",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM encounters, patients",
        ["encounters", "patients"],
        "unsafe_join",
        id="comma_join",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM encounters JOIN patients ON TRUE",
        ["encounters", "patients"],
        "unsafe_join",
        id="join_on_true",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM encounters JOIN patients ON 1 = 1",
        ["encounters", "patients"],
        "unsafe_join",
        id="join_on_literal_equality",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM encounters AS e JOIN patients AS p "
        "ON e.patient_id > p.patient_id",
        ["encounters", "patients"],
        "unsafe_join",
        id="non_equality_join",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM encounters AS e JOIN patients AS p "
        "ON e.patient_id = p.patient_id OR TRUE",
        ["encounters", "patients"],
        "unsafe_join",
        id="join_equality_or_true",
    ),
    pytest.param(
        "SELECT COUNT(*) FROM encounters AS e JOIN patients AS p "
        "ON e.patient_id = p.patient_id OR p.patient_id = 'target'",
        ["encounters", "patients"],
        "unsafe_join",
        id="join_with_identifier_probe",
    ),
    # Recursion, remote functions, deliberate errors, and system metadata.
    pytest.param(
        "WITH RECURSIVE nums AS ("
        "SELECT 1 AS n UNION ALL SELECT n + 1 FROM nums WHERE n < 1000000"
        ") SELECT COUNT(*) FROM nums",
        [],
        "recursive_cte_not_allowed",
        id="recursive_cte",
    ),
    pytest.param(
        "SELECT COUNT(*), evil_remote(status) FROM appointments",
        ["appointments"],
        "unsafe_sql_function",
        id="unknown_function",
    ),
    pytest.param(
        "SELECT COUNT(*), project.dataset.remote_fn(status) FROM appointments",
        ["appointments"],
        "unsafe_sql_function",
        id="qualified_remote_function",
    ),
    pytest.param(
        "SELECT COUNT(*), SESSION_USER() FROM appointments",
        ["appointments"],
        "unsafe_sql_function",
        id="session_user",
    ),
    pytest.param(
        "SELECT COUNT(*), ERROR('forced failure') FROM appointments",
        ["appointments"],
        "unsafe_sql_function",
        id="deliberate_error",
    ),
    pytest.param(
        "SELECT COUNT(*), @@project_id FROM appointments",
        ["appointments"],
        "unsafe_system_variable",
        id="system_project_id",
    ),
    pytest.param(
        "SELECT COUNT(*), @@dataset_id FROM appointments",
        ["appointments"],
        "unsafe_system_variable",
        id="system_dataset_id",
    ),
]


@pytest.mark.parametrize(
    ("sql", "tables", "expected_reason"),
    MALICIOUS_SQL_CASES,
)
def test_malicious_sql_is_always_blocked(
    sql: str,
    tables: list[str],
    expected_reason: str | None,
) -> None:
    result = validate_sql(sql, tables)

    assert result["allowed"] is False
    assert result["normalized_sql"] is None
    assert result["reason"]
    assert result["violations"]
    assert result["violations"][0]["code"] == result["reason"]
    if expected_reason is not None:
        assert result["reason"] == expected_reason


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM appointments "
        "WHERE status = '; DROP TABLE appointments'",
        "SELECT COUNT(*) FROM appointments "
        "WHERE status = 'DELETE FROM patients' /* UPDATE appointments */",
        "SELECT COUNT(*) AS `drop`, COUNTIF(status = 'update') "
        "FROM appointments",
        "SELECT COUNT(*) FROM appointments -- EXECUTE IMMEDIATE is only a comment",
        "SELECT COUNT(*) FROM appointments WHERE status = @status",
    ],
    ids=[
        "semicolon_and_ddl_in_literal",
        "dml_in_literal_and_comment",
        "keyword_alias_and_literal",
        "script_keyword_in_comment",
        "ordinary_query_parameter",
    ],
)
def test_injection_like_data_does_not_create_false_positives(sql: str) -> None:
    result = validate_sql(sql, ["appointments"])

    assert result["allowed"] is True
    assert result["violations"] == []


def test_duplicate_declared_tables_are_blocked() -> None:
    result = validate_sql(
        "SELECT COUNT(*) FROM appointments",
        ["appointments", "appointments"],
    )

    assert result["reason"] == "invalid_table_declaration"


@pytest.mark.parametrize(
    ("sql", "tables"),
    [
        (" ", []),
        ("S" * 10_001, []),
        ("SELECT COUNT(*) FROM appointments", ["a", "b", "c", "d"]),
        ("SELECT COUNT(*) FROM appointments", ["x" * 257]),
    ],
    ids=["blank_sql", "oversized_sql", "too_many_declared_tables", "oversized_table_name"],
)
def test_malicious_input_boundaries_are_rejected(
    sql: str, tables: list[str]
) -> None:
    with pytest.raises(ValidationError):
        validate_sql(sql, tables)

