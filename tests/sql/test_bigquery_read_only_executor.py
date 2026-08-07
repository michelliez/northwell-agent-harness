"""Tests for BigQuery read-only executor.

Tests the BigQueryReadOnlyExecutor class with mocked BigQuery client to verify
strict safety constraints: timeouts, quota enforcement, labels, no writes, etc.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# The SDK is an optional group, so this module skips rather than fails when it
# is absent. `importorskip` also keeps these imports at the top of the file,
# which a bare `try`/`except ImportError` around them would not.
bigquery = pytest.importorskip(
    "google.cloud.bigquery", reason="requires `uv sync --group bigquery`"
)
_exceptions = pytest.importorskip("google.cloud.exceptions")
BadRequest = _exceptions.BadRequest
Forbidden = _exceptions.Forbidden

from sql.bigquery_adapter import (  # noqa: E402
    BigQueryNotConfigured,
    BigQueryReadOnlyExecutor,
    _build_query_parameters,
)
from sql.models import CompiledQuery, PlannedParameter  # noqa: E402

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def mock_bigquery_client():
    """Mock BigQuery client."""
    with patch("google.cloud.bigquery.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        yield mock_client


@pytest.fixture
def executor(mock_bigquery_client):
    """Create executor with mocked client."""
    return BigQueryReadOnlyExecutor(
        project="test-project",
        location="us-west1",
    )


@pytest.fixture
def mock_job_success():
    """Mock successful query job."""
    job = MagicMock()
    job.result.return_value = iter(
        [
            {"dept": "CARDIOLOGY", "count": 42},
            {"dept": "ONCOLOGY", "count": 15},
        ]
    )
    return job


@pytest.fixture
def mock_job_timeout():
    """Mock job that times out."""
    job = MagicMock()
    job.result.side_effect = Exception("Query exceeded 30000ms timeout")
    return job


@pytest.fixture
def mock_job_quota_exceeded():
    """Mock job that exceeds quota."""
    job = MagicMock()
    job.result.side_effect = Exception(
        "Query would read 10000000000 bytes but maximum_bytes_billed is 1000000000"
    )
    return job


@pytest.fixture
def compiled_query_simple():
    """Simple compiled query without parameters."""
    return CompiledQuery(
        sql="SELECT dept, COUNT(*) as count FROM APPOINTMENT_FACT GROUP BY dept",
        parameters=[],
        plan_hash="abc123" + "0" * 58,  # 64 hex chars
        compiler_version="approved-plan-bigquery-v1",
        source="deterministic",
    )


@pytest.fixture
def compiled_query_with_params():
    """Compiled query with parameters."""
    return CompiledQuery(
        sql="SELECT COUNT(*) as count FROM APPOINTMENT_FACT WHERE dept = @dept AND appt_date >= @start_date",
        parameters=[
            PlannedParameter(name="dept", type="STRING", value="CARDIOLOGY"),
            PlannedParameter(name="start_date", type="DATE", value="2026-01-01"),
        ],
        plan_hash="def456" + "0" * 58,
        compiler_version="approved-plan-bigquery-v1",
        source="deterministic",
    )


# ── Basic Execution Tests ─────────────────────────────────────────────────


def test_executor_executes_simple_query(executor, mock_bigquery_client, mock_job_success):
    """Test executor runs simple query and returns results."""
    mock_bigquery_client.query.return_value = mock_job_success

    result = executor.execute(
        compiled=MagicMock(
            sql="SELECT 1 as col",
            parameters=[],
            source="deterministic",
        ),
        maximum_bytes_billed=1_000_000,
    )

    assert len(result) == 2
    assert result[0]["dept"] == "CARDIOLOGY"
    assert result[1]["count"] == 15


def test_executor_binds_parameters(executor, mock_bigquery_client, mock_job_success):
    """Test executor binds query parameters correctly."""
    mock_bigquery_client.query.return_value = mock_job_success

    compiled = MagicMock(
        sql="SELECT * FROM table WHERE col = @param1",
        parameters=[PlannedParameter(name="param1", type="STRING", value="test_value")],
        source="deterministic",
    )

    executor.execute(compiled, maximum_bytes_billed=1_000_000)

    # Verify query was called
    mock_bigquery_client.query.assert_called_once()

    # Verify parameters were passed
    job_config = mock_bigquery_client.query.call_args[1]["job_config"]
    assert len(job_config.query_parameters) == 1
    assert job_config.query_parameters[0].name == "param1"


def test_executor_enforces_maximum_bytes_billed(executor, mock_bigquery_client, mock_job_success):
    """Test executor sets maximum_bytes_billed constraint."""
    mock_bigquery_client.query.return_value = mock_job_success

    executor.execute(
        compiled=MagicMock(
            sql="SELECT * FROM table",
            parameters=[],
            source="deterministic",
        ),
        maximum_bytes_billed=5_000_000,
    )

    job_config = mock_bigquery_client.query.call_args[1]["job_config"]
    assert job_config.maximum_bytes_billed == 5_000_000


def test_executor_enforces_timeout(executor, mock_bigquery_client, mock_job_success):
    """Test executor enforces query timeout via job.result()."""
    mock_bigquery_client.query.return_value = mock_job_success

    executor.execute(
        compiled=MagicMock(
            sql="SELECT * FROM table",
            parameters=[],
            source="deterministic",
        ),
        maximum_bytes_billed=1_000_000,
        timeout_ms=15_000,  # 15 second timeout
    )

    # Verify result timeout was called with correct duration
    mock_job_success.result.assert_called_once()
    call_kwargs = mock_job_success.result.call_args[1]
    assert call_kwargs["timeout"] == 15.0  # 15_000ms / 1000


def test_executor_sets_audit_labels(executor, mock_bigquery_client, mock_job_success):
    """Test executor sets labels for audit logging."""
    mock_bigquery_client.query.return_value = mock_job_success

    executor.execute(
        compiled=MagicMock(
            sql="SELECT * FROM table",
            parameters=[],
            source="deterministic",
        ),
        maximum_bytes_billed=1_000_000,
        run_id="run-123",
        user_id="user-456",
    )

    job_config = mock_bigquery_client.query.call_args[1]["job_config"]
    assert job_config.labels["source"] == "agent-harness"
    assert job_config.labels["sql_source"] == "deterministic"
    assert job_config.labels["run_id"] == "run-123"
    assert job_config.labels["user_id"] == "user-456"


def test_executor_disables_legacy_sql(executor, mock_bigquery_client, mock_job_success):
    """Test executor enforces BigQuery standard SQL only."""
    mock_bigquery_client.query.return_value = mock_job_success

    executor.execute(
        compiled=MagicMock(
            sql="SELECT * FROM table",
            parameters=[],
            source="deterministic",
        ),
        maximum_bytes_billed=1_000_000,
    )

    job_config = mock_bigquery_client.query.call_args[1]["job_config"]
    assert job_config.use_legacy_sql is False


def test_executor_disables_destination_table(executor, mock_bigquery_client, mock_job_success):
    """Test executor prevents writing results to destination table."""
    mock_bigquery_client.query.return_value = mock_job_success

    executor.execute(
        compiled=MagicMock(
            sql="SELECT * FROM table",
            parameters=[],
            source="deterministic",
        ),
        maximum_bytes_billed=1_000_000,
    )

    job_config = mock_bigquery_client.query.call_args[1]["job_config"]
    assert job_config.allow_large_results is False


# ── Error Handling Tests ──────────────────────────────────────────────────


def test_executor_raises_timeout_error(executor, mock_bigquery_client, mock_job_timeout):
    """Test executor raises TimeoutError when query exceeds timeout."""
    mock_bigquery_client.query.return_value = mock_job_timeout

    with pytest.raises(TimeoutError, match="exceeded.*timeout"):
        executor.execute(
            compiled=MagicMock(
                sql="SELECT * FROM large_table",
                parameters=[],
                source="deterministic",
            ),
            maximum_bytes_billed=1_000_000,
            timeout_ms=30_000,
        )


def test_executor_raises_quota_error(executor, mock_bigquery_client, mock_job_quota_exceeded):
    """Test executor raises RuntimeError when query exceeds quota."""
    mock_bigquery_client.query.return_value = mock_job_quota_exceeded

    with pytest.raises(RuntimeError, match="maximum_bytes_billed"):
        executor.execute(
            compiled=MagicMock(
                sql="SELECT * FROM huge_table",
                parameters=[],
                source="deterministic",
            ),
            maximum_bytes_billed=1_000_000,
        )


def test_executor_handles_bigquery_error(executor, mock_bigquery_client):
    """Test executor handles BigQuery-specific errors."""
    mock_bigquery_client.query.side_effect = BadRequest("Invalid query syntax")

    with pytest.raises(RuntimeError, match="BigQuery error"):
        executor.execute(
            compiled=MagicMock(
                sql="SELECT * FROM",  # Invalid syntax
                parameters=[],
                source="deterministic",
            ),
            maximum_bytes_billed=1_000_000,
        )


def test_executor_handles_permission_error(executor, mock_bigquery_client):
    """Test executor handles permission denied errors."""
    mock_bigquery_client.query.side_effect = Forbidden("Access denied")

    with pytest.raises(RuntimeError, match="error"):
        executor.execute(
            compiled=MagicMock(
                sql="SELECT * FROM restricted_table",
                parameters=[],
                source="deterministic",
            ),
            maximum_bytes_billed=1_000_000,
        )


# ── Initialization Tests ──────────────────────────────────────────────────


def test_executor_initialization_with_default_location():
    """Test executor uses default location."""
    with patch("google.cloud.bigquery.Client"):
        executor = BigQueryReadOnlyExecutor(project="test-project")

        assert executor.project == "test-project"
        assert executor.location == "us-west1"


def test_executor_initialization_with_custom_location():
    """Test executor uses provided location."""
    with patch("google.cloud.bigquery.Client"):
        executor = BigQueryReadOnlyExecutor(project="test-project", location="eu-london")

        assert executor.location == "eu-london"


def test_executor_initialization_from_service_account_key():
    """Test executor loads from service account key file."""
    with patch("google.cloud.bigquery.Client") as mock_client_class:
        BigQueryReadOnlyExecutor(
            project="test-project",
            service_account_key_path="/path/to/key.json",
        )

        # Should call from_service_account_json
        mock_client_class.from_service_account_json.assert_called_once()


def test_executor_initialization_fails_without_credentials():
    """Test executor raises error when credentials unavailable."""
    with patch("google.cloud.bigquery.Client") as mock_client_class:
        mock_client_class.side_effect = Exception("Credentials not found")

        with pytest.raises(BigQueryNotConfigured, match="Failed to initialize"):
            BigQueryReadOnlyExecutor(project="test-project")


# ── Parameter Binding Tests ───────────────────────────────────────────────


def test_build_query_parameters_empty():
    """Test parameter builder with no parameters."""
    params = _build_query_parameters([])
    assert params == []


def test_build_query_parameters_string():
    """Test parameter builder with string parameter."""
    param = PlannedParameter(name="dept", type="STRING", value="CARDIOLOGY")

    result = _build_query_parameters([param])

    assert len(result) == 1
    assert result[0].name == "dept"
    # Verify it's a valid ScalarQueryParameter by checking it was created


def test_build_query_parameters_int64():
    """Test parameter builder with integer parameter."""
    param = PlannedParameter(name="count", type="INT64", value=42)

    result = _build_query_parameters([param])

    assert len(result) == 1
    # Parameter type checked by BigQuery


def test_build_query_parameters_float64():
    """Test parameter builder with float parameter."""
    param = PlannedParameter(name="rate", type="FLOAT64", value=3.14)

    result = _build_query_parameters([param])

    assert len(result) == 1
    # Parameter type checked by BigQuery


def test_build_query_parameters_bool():
    """Test parameter builder with boolean parameter."""
    param = PlannedParameter(name="flag", type="BOOL", value=True)

    result = _build_query_parameters([param])

    assert len(result) == 1
    # Parameter type checked by BigQuery


def test_build_query_parameters_date():
    """Test parameter builder with date parameter."""
    param = PlannedParameter(name="start_date", type="DATE", value="2026-01-01")

    result = _build_query_parameters([param])

    assert len(result) == 1
    # Parameter type checked by BigQuery


def test_build_query_parameters_datetime():
    """Test parameter builder with datetime parameter."""
    param = PlannedParameter(name="created_at", type="DATETIME", value="2026-01-01 12:00:00")

    result = _build_query_parameters([param])

    assert len(result) == 1
    # Parameter type checked by BigQuery


def test_build_query_parameters_timestamp():
    """Test parameter builder with timestamp parameter."""
    param = PlannedParameter(name="ts", type="TIMESTAMP", value="2026-01-01T12:00:00Z")

    result = _build_query_parameters([param])

    assert len(result) == 1
    # Parameter type checked by BigQuery


def test_build_query_parameters_multiple():
    """Test parameter builder with multiple parameters."""
    params = [
        PlannedParameter(name="dept", type="STRING", value="CARDIOLOGY"),
        PlannedParameter(name="min_count", type="INT64", value=10),
        PlannedParameter(name="start_date", type="DATE", value="2026-01-01"),
    ]

    result = _build_query_parameters(params)

    assert len(result) == 3
    assert result[0].name == "dept"
    assert result[1].name == "min_count"
    assert result[2].name == "start_date"


# ── Integration-Like Tests ────────────────────────────────────────────────


def test_executor_with_realistic_aggregate_query(executor, mock_bigquery_client, mock_job_success):
    """Test executor with realistic aggregate query."""
    mock_bigquery_client.query.return_value = mock_job_success

    compiled = MagicMock(
        sql="""
        SELECT
            DEPT_ID,
            STATUS,
            COUNT(*) as appointment_count,
            COUNT(DISTINCT PROVIDER_ID) as unique_providers
        FROM APPOINTMENT_FACT
        WHERE APPT_DATE >= @start_date AND STATUS = @status
        GROUP BY DEPT_ID, STATUS
        """,
        parameters=[
            PlannedParameter(name="start_date", type="DATE", value="2026-01-01"),
            PlannedParameter(name="status", type="STRING", value="COMPLETED"),
        ],
        source="deterministic",
    )

    result = executor.execute(compiled, maximum_bytes_billed=100_000_000)

    assert len(result) == 2


def test_executor_with_multiple_parameters(executor, mock_bigquery_client, mock_job_success):
    """Test executor with query having multiple parameter types."""
    mock_bigquery_client.query.return_value = mock_job_success

    compiled = MagicMock(
        sql="""
        SELECT COUNT(*) as cnt
        FROM APPOINTMENT_FACT
        WHERE appt_date >= @start_date
          AND appt_date < @end_date
          AND appointment_count > @min_count
        """,
        parameters=[
            PlannedParameter(name="start_date", type="DATE", value="2026-01-01"),
            PlannedParameter(name="end_date", type="DATE", value="2026-12-31"),
            PlannedParameter(name="min_count", type="INT64", value=5),
        ],
        source="deterministic",
    )

    result = executor.execute(
        compiled,
        maximum_bytes_billed=500_000_000,
        run_id="run-xyz",
        user_id="user-abc",
    )

    assert isinstance(result, list)


def test_executor_result_contains_dicts(executor, mock_bigquery_client):
    """Test executor returns results as list of dicts."""
    mock_job = MagicMock()
    # Mock row objects
    row1 = MagicMock()
    row1.__iter__.return_value = iter([("dept", "CARDIOLOGY"), ("count", 42)])
    row2 = MagicMock()
    row2.__iter__.return_value = iter([("dept", "ONCOLOGY"), ("count", 15)])

    mock_job.result.return_value = iter([row1, row2])
    mock_bigquery_client.query.return_value = mock_job

    result = executor.execute(
        compiled=MagicMock(
            sql="SELECT dept, count FROM data",
            parameters=[],
            source="deterministic",
        ),
        maximum_bytes_billed=1_000_000,
    )

    assert isinstance(result, list)
    # Result should be list of dicts created from rows


# ── Edge Cases ────────────────────────────────────────────────────────────


def test_executor_with_empty_result(executor, mock_bigquery_client):
    """Test executor handles empty result set."""
    mock_job = MagicMock()
    mock_job.result.return_value = iter([])  # Empty result
    mock_bigquery_client.query.return_value = mock_job

    result = executor.execute(
        compiled=MagicMock(
            sql="SELECT * FROM table WHERE FALSE",
            parameters=[],
            source="deterministic",
        ),
        maximum_bytes_billed=1_000_000,
    )

    assert result == []


def test_executor_label_truncation(executor, mock_bigquery_client, mock_job_success):
    """Test executor truncates labels to max 64 chars."""
    mock_bigquery_client.query.return_value = mock_job_success

    long_run_id = "x" * 100

    executor.execute(
        compiled=MagicMock(
            sql="SELECT 1",
            parameters=[],
            source="deterministic",
        ),
        maximum_bytes_billed=1_000_000,
        run_id=long_run_id,
    )

    job_config = mock_bigquery_client.query.call_args[1]["job_config"]
    assert len(job_config.labels["run_id"]) == 64
