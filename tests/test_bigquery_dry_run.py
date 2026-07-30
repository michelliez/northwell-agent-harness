"""Tests for BigQuery dry-run adapter.

Tests the dry_run function with mocked BigQuery client to avoid needing
real GCP credentials during testing.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from google.cloud import bigquery
from google.cloud.exceptions import BadRequest, NotFound

from sql.bigquery_adapter import BigQueryNotConfigured, dry_run
from sql.models import DryRunResult


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def mock_bigquery_client():
    """Mock BigQuery client."""
    with patch("sql.bigquery_adapter.bigquery.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_job_valid():
    """Mock successful dry-run job."""
    job = MagicMock()
    job.total_bytes_processed = 1_000_000  # 1 MB
    job.referenced_tables = []
    return job


@pytest.fixture
def mock_job_large():
    """Mock dry-run job with large bytes."""
    job = MagicMock()
    job.total_bytes_processed = 50_000_000_000  # 50 GB
    job.referenced_tables = []
    return job


@pytest.fixture
def mock_job_with_tables():
    """Mock dry-run job that references tables."""
    job = MagicMock()
    job.total_bytes_processed = 5_000_000  # 5 MB

    # Mock table references
    table1 = MagicMock()
    table1.project = "test-project"
    table1.dataset_id = "dataset1"
    table1.table_id = "APPOINTMENT_FACT"

    table2 = MagicMock()
    table2.project = "test-project"
    table2.dataset_id = "dataset1"
    table2.table_id = "PROVIDER"

    job.referenced_tables = [table1, table2]
    return job


# ── Basic Functionality Tests ──────────────────────────────────────────────


def test_dry_run_valid_sql(mock_bigquery_client, mock_job_valid):
    """Test dry-run with valid SQL."""
    mock_bigquery_client.query.return_value = mock_job_valid

    result = dry_run(
        "SELECT COUNT(*) FROM APPOINTMENT_FACT",
        project="test-project",
        location="us-west1",
    )

    assert result.valid is True
    assert result.total_bytes_processed == 1_000_000
    assert result.project == "test-project"
    assert result.location == "us-west1"
    assert result.error is None
    assert result.referenced_tables == []


def test_dry_run_with_referenced_tables(mock_bigquery_client, mock_job_with_tables):
    """Test dry-run extracts referenced tables."""
    mock_bigquery_client.query.return_value = mock_job_with_tables

    result = dry_run(
        "SELECT * FROM APPOINTMENT_FACT JOIN PROVIDER USING (PROVIDER_ID)",
        project="test-project",
        location="us-west1",
    )

    assert result.valid is True
    assert len(result.referenced_tables) == 2
    assert "APPOINTMENT_FACT" in result.referenced_tables[0]
    assert "PROVIDER" in result.referenced_tables[1]


def test_dry_run_large_query_bytes(mock_bigquery_client, mock_job_large):
    """Test dry-run captures large byte estimates."""
    mock_bigquery_client.query.return_value = mock_job_large

    result = dry_run(
        "SELECT * FROM LARGE_TABLE",
        project="test-project",
        location="us-west1",
    )

    assert result.valid is True
    assert result.total_bytes_processed == 50_000_000_000


# ── Error Handling Tests ──────────────────────────────────────────────────


def test_dry_run_invalid_sql(mock_bigquery_client):
    """Test dry-run with syntactically invalid SQL."""
    mock_bigquery_client.query.side_effect = BadRequest("Syntax error in SQL")

    result = dry_run(
        "SELECT * FROM nonexistent_table",
        project="test-project",
        location="us-west1",
    )

    assert result.valid is False
    assert result.error is not None
    assert "Syntax error" in result.error
    assert result.total_bytes_processed is None


def test_dry_run_table_not_found(mock_bigquery_client):
    """Test dry-run when table does not exist."""
    mock_bigquery_client.query.side_effect = NotFound("Table not found")

    result = dry_run(
        "SELECT * FROM fake_dataset.fake_table",
        project="test-project",
        location="us-west1",
    )

    assert result.valid is False
    assert result.error is not None
    assert "not found" in result.error.lower()


def test_dry_run_permission_denied(mock_bigquery_client):
    """Test dry-run when access is denied."""
    mock_bigquery_client.query.side_effect = BadRequest(
        "Access Denied: User does not have bigquery.datasets.get permission"
    )

    result = dry_run(
        "SELECT * FROM restricted_dataset.restricted_table",
        project="test-project",
        location="us-west1",
    )

    assert result.valid is False
    assert "Access Denied" in result.error


# ── Configuration Tests ────────────────────────────────────────────────────


def test_dry_run_default_project_from_env(mock_bigquery_client, mock_job_valid):
    """Test dry-run uses GOOGLE_CLOUD_PROJECT env var if project not provided."""
    mock_bigquery_client.query.return_value = mock_job_valid

    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "env-project"}):
        result = dry_run(
            "SELECT COUNT(*) FROM APPOINTMENT_FACT",
            location="us-west1",
        )

    assert result.valid is True
    assert result.project == "env-project"


def test_dry_run_default_location_from_env(mock_bigquery_client, mock_job_valid):
    """Test dry-run uses BIGQUERY_LOCATION env var if location not provided."""
    mock_bigquery_client.query.return_value = mock_job_valid

    with patch.dict(os.environ, {"BIGQUERY_LOCATION": "us-east1"}):
        result = dry_run(
            "SELECT COUNT(*) FROM APPOINTMENT_FACT",
            project="test-project",
        )

    assert result.valid is True
    assert result.location == "us-east1"


def test_dry_run_default_location_fallback(mock_bigquery_client, mock_job_valid):
    """Test dry-run defaults to us-west1 if location not provided."""
    mock_bigquery_client.query.return_value = mock_job_valid

    with patch.dict(os.environ, {}, clear=False):
        # Remove BIGQUERY_LOCATION if it exists
        os.environ.pop("BIGQUERY_LOCATION", None)

        result = dry_run(
            "SELECT COUNT(*) FROM APPOINTMENT_FACT",
            project="test-project",
        )

    assert result.valid is True
    assert result.location == "us-west1"


def test_dry_run_missing_project_raises(mock_bigquery_client):
    """Test dry-run raises if project cannot be determined."""
    with patch.dict(os.environ, {}, clear=True):
        # Remove all env vars
        with pytest.raises(BigQueryNotConfigured, match="project must be provided"):
            dry_run("SELECT COUNT(*) FROM APPOINTMENT_FACT")


def test_dry_run_client_initialization_error(mock_bigquery_client):
    """Test dry-run handles client initialization errors."""
    with patch("sql.bigquery_adapter.bigquery.Client") as mock_client_class:
        mock_client_class.side_effect = Exception("Failed to load credentials")

        with pytest.raises(BigQueryNotConfigured, match="Failed to initialize"):
            dry_run(
                "SELECT COUNT(*) FROM APPOINTMENT_FACT",
                project="test-project",
            )


# ── DryRunResult Model Validation Tests ────────────────────────────────────


def test_dry_run_result_valid_state():
    """Test DryRunResult validation: valid=True requires bytes, no error."""
    result = DryRunResult(
        valid=True,
        total_bytes_processed=1_000_000,
        project="test-project",
        location="us-west1",
    )

    # Should not raise
    assert result.valid is True


def test_dry_run_result_invalid_state_requires_error():
    """Test DryRunResult validation: valid=False requires error."""
    with pytest.raises(ValueError, match="invalid dry runs require an error"):
        DryRunResult(valid=False)


def test_dry_run_result_valid_requires_bytes():
    """Test DryRunResult validation: valid=True requires bytes."""
    with pytest.raises(ValueError, match="valid dry runs require bytes"):
        DryRunResult(valid=True)


def test_dry_run_result_valid_cannot_have_error():
    """Test DryRunResult validation: valid=True cannot have error."""
    with pytest.raises(ValueError, match="cannot contain an error"):
        DryRunResult(valid=True, total_bytes_processed=1_000_000, error="some error")


# ── Query Job Config Tests ────────────────────────────────────────────────


def test_dry_run_sets_correct_job_config(mock_bigquery_client, mock_job_valid):
    """Test dry-run configures job correctly."""
    mock_bigquery_client.query.return_value = mock_job_valid

    dry_run(
        "SELECT COUNT(*) FROM APPOINTMENT_FACT",
        project="test-project",
        location="us-west1",
    )

    # Verify query was called
    mock_bigquery_client.query.assert_called_once()

    # Verify SQL was passed
    call_args = mock_bigquery_client.query.call_args
    assert call_args[0][0] == "SELECT COUNT(*) FROM APPOINTMENT_FACT"

    # Verify job config
    job_config = call_args[1]["job_config"]
    assert job_config.dry_run is True
    assert job_config.use_legacy_sql is False

    # Verify location was passed as a parameter (not in job_config)
    assert call_args[1]["location"] == "us-west1"


def test_dry_run_with_cte(mock_bigquery_client, mock_job_valid):
    """Test dry-run with Common Table Expression."""
    mock_bigquery_client.query.return_value = mock_job_valid

    sql = """
    WITH grouped AS (
        SELECT DEPT_ID, COUNT(*) as cnt
        FROM APPOINTMENT_FACT
        GROUP BY DEPT_ID
    )
    SELECT * FROM grouped
    """

    result = dry_run(sql, project="test-project", location="us-west1")

    assert result.valid is True
    assert result.total_bytes_processed == 1_000_000


def test_dry_run_with_join(mock_bigquery_client, mock_job_valid):
    """Test dry-run with JOIN."""
    mock_bigquery_client.query.return_value = mock_job_valid

    sql = """
    SELECT a.APPT_ID, p.PROVIDER_ID
    FROM APPOINTMENT_FACT a
    JOIN PROVIDER p ON a.PROVIDER_ID = p.PROVIDER_ID
    """

    result = dry_run(sql, project="test-project", location="us-west1")

    assert result.valid is True


def test_dry_run_with_parameters(mock_bigquery_client, mock_job_valid):
    """Test dry-run with query parameters (dry-run should still work)."""
    mock_bigquery_client.query.return_value = mock_job_valid

    sql = """
    SELECT COUNT(*) as cnt
    FROM APPOINTMENT_FACT
    WHERE APPT_DATE >= @start_date
    """

    result = dry_run(sql, project="test-project", location="us-west1")

    assert result.valid is True


# ── Edge Cases ──────────────────────────────────────────────────────────────


def test_dry_run_empty_result_set(mock_bigquery_client, mock_job_valid):
    """Test dry-run when query would return no rows."""
    mock_job_valid.total_bytes_processed = 100  # Small result
    mock_bigquery_client.query.return_value = mock_job_valid

    result = dry_run(
        "SELECT * FROM APPOINTMENT_FACT WHERE FALSE",
        project="test-project",
        location="us-west1",
    )

    assert result.valid is True
    assert result.total_bytes_processed == 100


def test_dry_run_zero_bytes_valid(mock_bigquery_client, mock_job_valid):
    """Test dry-run handles zero bytes (e.g., COUNT(*) on empty table)."""
    mock_job_valid.total_bytes_processed = 0
    mock_bigquery_client.query.return_value = mock_job_valid

    result = dry_run(
        "SELECT COUNT(*) FROM empty_table",
        project="test-project",
        location="us-west1",
    )

    assert result.valid is True
    assert result.total_bytes_processed == 0


def test_dry_run_multistatement_sql_error(mock_bigquery_client):
    """Test dry-run rejects multi-statement SQL."""
    # BigQuery should reject this
    mock_bigquery_client.query.side_effect = BadRequest(
        "Unexpected end of statement"
    )

    result = dry_run(
        "SELECT 1; SELECT 2;",
        project="test-project",
        location="us-west1",
    )

    assert result.valid is False
    assert result.error is not None


def test_dry_run_different_locations(mock_bigquery_client, mock_job_valid):
    """Test dry-run respects location parameter."""
    mock_bigquery_client.query.return_value = mock_job_valid

    locations = ["us-west1", "us-east1", "eu-london", "asia-southeast1"]

    for location in locations:
        result = dry_run(
            "SELECT COUNT(*) FROM APPOINTMENT_FACT",
            project="test-project",
            location=location,
        )

        assert result.valid is True
        assert result.location == location


# ── Integration-Like Tests (Still Mocked) ──────────────────────────────────


def test_dry_run_aggregate_query(mock_bigquery_client, mock_job_valid):
    """Test dry-run on a realistic aggregate query."""
    mock_bigquery_client.query.return_value = mock_job_valid

    sql = """
    SELECT
        DEPT_ID,
        STATUS,
        COUNT(*) as appointment_count,
        COUNT(DISTINCT PROVIDER_ID) as unique_providers
    FROM APPOINTMENT_FACT
    WHERE APPT_DATE >= '2026-01-01'
    GROUP BY DEPT_ID, STATUS
    """

    result = dry_run(sql, project="test-project", location="us-west1")

    assert result.valid is True
    assert result.total_bytes_processed > 0


def test_dry_run_multi_table_join_query(mock_bigquery_client, mock_job_with_tables):
    """Test dry-run on complex multi-table join."""
    mock_bigquery_client.query.return_value = mock_job_with_tables

    sql = """
    SELECT
        d.DEPT_ID,
        d.DEPT_NAME,
        COUNT(DISTINCT a.APPOINTMENT_ID) as appointment_count
    FROM APPOINTMENT_FACT a
    JOIN PROVIDER p ON a.PROVIDER_ID = p.PROVIDER_ID
    JOIN DEPT d ON p.DEPT_ID = d.DEPT_ID
    WHERE a.APPT_DATE >= '2026-01-01'
    GROUP BY d.DEPT_ID, d.DEPT_NAME
    """

    result = dry_run(sql, project="test-project", location="us-west1")

    assert result.valid is True
    assert len(result.referenced_tables) > 0
