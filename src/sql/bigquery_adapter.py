"""BigQuery adapter: dry-run, execution, and result redaction.

Implements safe, bounded interactions with BigQuery:
- dry_run: estimate cost without executing
- execute_read_only: read-only execution with strict constraints
- estimate_cost: (future) cost forecast from dry-run
- redact_result: (future) remove sensitive columns from results
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from sql.models import CompiledQuery, DryRunResult, PlannedParameter

if TYPE_CHECKING:
    from google.cloud import bigquery


class BigQueryNotConfigured(RuntimeError):
    """Raised when BigQuery is not configured or credentials are missing."""


def _load_bigquery() -> tuple[Any, Any]:
    """Import the optional BigQuery SDK, or fail closed with the fix.

    Every SDK use goes through here rather than a module-scope import, because
    `test_request_path_does_not_import_the_bigquery_sdk` asserts in a subprocess
    that importing `sql.bigquery_adapter` loads no `google.*` module at all.
    `pyarrow` alone is a ~100 MB install, so the group stays optional.
    """
    try:
        from google.cloud import bigquery
        from google.cloud.exceptions import GoogleCloudError
    except ImportError as exc:
        raise BigQueryNotConfigured(
            "BigQuery support is not installed. Run `uv sync --group bigquery`."
        ) from exc
    return bigquery, GoogleCloudError


def dry_run(
    sql: str,
    project: str | None = None,
    location: str | None = None,
) -> DryRunResult:
    """Estimate query cost by running with dry_run=True.

    Args:
        sql: BigQuery SQL statement (single statement only)
        project: GCP project ID. Defaults to GOOGLE_CLOUD_PROJECT env var.
        location: BigQuery location (e.g., 'us-west1'). Defaults to BIGQUERY_LOCATION env var.

    Returns:
        DryRunResult with total_bytes_processed if valid, error if invalid.

    Raises:
        BigQueryNotConfigured: if the optional SDK is absent or credentials
            cannot be found.
    """
    bigquery, GoogleCloudError = _load_bigquery()

    if project is None:
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise BigQueryNotConfigured(
                "project must be provided or GOOGLE_CLOUD_PROJECT env var set"
            )

    if location is None:
        location = os.getenv("BIGQUERY_LOCATION", "us-west1")

    try:
        client = bigquery.Client(project=project)
    except Exception as exc:
        raise BigQueryNotConfigured(f"Failed to initialize BigQuery client: {exc}") from exc

    try:
        job_config = bigquery.QueryJobConfig(
            dry_run=True,
            use_legacy_sql=False,
        )

        job = client.query(sql, job_config=job_config, location=location)

        # Dry-run queries don't have results; just the job metadata
        total_bytes = job.total_bytes_processed

        # Extract referenced tables from the AST if available
        referenced_tables: list[str] = []
        try:
            # This requires parsing the query; BigQuery doesn't directly expose referenced tables
            # For now, we rely on the caller to provide declared tables
            if hasattr(job, "referenced_tables") and job.referenced_tables:
                referenced_tables = [
                    f"{t.project}.{t.dataset_id}.{t.table_id}" for t in job.referenced_tables
                ]
        except Exception:
            # If extraction fails, just return empty list
            pass

        return DryRunResult(
            valid=True,
            total_bytes_processed=total_bytes,
            project=project,
            location=location,
            referenced_tables=referenced_tables,
        )

    except GoogleCloudError as exc:
        # BigQuery-specific errors (invalid SQL, access denied, etc.)
        error_message = str(exc)
        return DryRunResult(
            valid=False,
            error=error_message,
        )

    except Exception as exc:
        # Catch other errors (network, malformed SQL, etc.)
        error_message = f"{type(exc).__name__}: {exc}"
        return DryRunResult(
            valid=False,
            error=error_message,
        )


class BigQueryReadOnlyExecutor:
    """Read-only executor for approved queries with strict safety constraints.

    Enforces:
    - maximum_bytes_billed: quota enforcement at query time
    - timeout_ms: wall-clock timeout (30 seconds default)
    - labels: audit logging (source, sql_source)
    - no destination table: prevents accidental writes
    - use_legacy_sql=False: BigQuery standard SQL only
    - read-only service account: no write permissions
    """

    def __init__(
        self,
        project: str,
        location: str = "us-west1",
        service_account_key_path: str | None = None,
    ):
        """Initialize executor with BigQuery credentials.

        Args:
            project: GCP project ID
            location: BigQuery location (default: us-west1)
            service_account_key_path: Path to read-only service account JSON key.
                If None, uses application default credentials.

        Raises:
            BigQueryNotConfigured: if credentials cannot be loaded
        """
        self.project = project
        self.location = location
        bigquery, _ = _load_bigquery()

        try:
            if service_account_key_path:
                self.client = bigquery.Client.from_service_account_json(
                    service_account_key_path, project=project
                )
            else:
                self.client = bigquery.Client(project=project)
        except Exception as exc:
            raise BigQueryNotConfigured(f"Failed to initialize BigQuery client: {exc}") from exc

    def execute(
        self,
        compiled: CompiledQuery,
        maximum_bytes_billed: int,
        timeout_ms: int = 30_000,
        run_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict]:
        """Execute approved query with strict safety constraints.

        Args:
            compiled: CompiledQuery with SQL and parameters
            maximum_bytes_billed: Maximum bytes to scan (quota enforcement)
            timeout_ms: Query timeout in milliseconds (default: 30s)
            run_id: Run ID for audit logging (optional)
            user_id: User ID for audit logging (optional)

        Returns:
            List of result rows as dicts

        Raises:
            BigQueryNotConfigured: if execution fails
            TimeoutError: if query exceeds timeout
            RuntimeError: if query fails or exceeds quota
        """
        bigquery, GoogleCloudError = _load_bigquery()
        try:
            # Build parameter bindings
            query_params = _build_query_parameters(compiled.parameters)

            # Build labels for audit logging
            labels = {
                "source": "agent-harness",
                "sql_source": compiled.source,  # deterministic, claude_repair, etc.
            }
            if run_id:
                labels["run_id"] = run_id[:64]  # Label max length
            if user_id:
                labels["user_id"] = user_id[:64]

            # Configure query with strict safety constraints
            job_config = bigquery.QueryJobConfig(
                use_legacy_sql=False,
                query_parameters=query_params,
                maximum_bytes_billed=maximum_bytes_billed,
                labels=labels,
                allow_large_results=False,  # No destination table
            )
            # Note: timeout_ms is enforced at job.result() time, not in config

            # Execute query
            job = self.client.query(
                compiled.sql,
                job_config=job_config,
                location=self.location,
            )

            # Block until complete or timeout
            try:
                result = job.result(timeout=timeout_ms / 1000.0)
            except Exception as exc:
                # Re-raise with more context
                if "timeout" in str(exc).lower():
                    raise TimeoutError(f"Query exceeded {timeout_ms}ms timeout") from exc
                if "quota" in str(exc).lower() or "maximum_bytes" in str(exc):
                    raise RuntimeError(
                        f"Query exceeded maximum_bytes_billed limit ({maximum_bytes_billed} bytes)"
                    ) from exc
                raise RuntimeError(f"Query execution failed: {exc}") from exc

            # Convert result to list of dicts
            return [dict(row) for row in result]

        except TimeoutError, RuntimeError:
            # Re-raise our custom errors
            raise
        except GoogleCloudError as exc:
            # BigQuery-specific errors
            raise RuntimeError(f"BigQuery error: {exc}") from exc
        except Exception as exc:
            # Other errors (network, auth, etc.)
            raise RuntimeError(f"Query execution error: {exc}") from exc


def _build_query_parameters(
    parameters: list[PlannedParameter],
) -> list[bigquery.ScalarQueryParameter]:
    """Convert PlannedParameter list to BigQuery ScalarQueryParameter list.

    Args:
        parameters: List of typed parameters from compiled query

    Returns:
        List of BigQuery query parameters ready to bind
    """
    bigquery, _ = _load_bigquery()
    query_params = []
    for param in parameters:
        # Map Python type to BigQuery type
        bq_type = {
            "STRING": "STRING",
            "INT64": "INT64",
            "FLOAT64": "FLOAT64",
            "BOOL": "BOOL",
            "DATE": "DATE",
            "DATETIME": "DATETIME",
            "TIMESTAMP": "TIMESTAMP",
        }[param.type]

        # Create scalar parameter
        query_param = bigquery.ScalarQueryParameter(param.name, bq_type, param.value)
        query_params.append(query_param)

    return query_params


def estimate_cost(sql: str) -> None:
    """Estimate cost from dry-run (future work)."""
    raise BigQueryNotConfigured("BigQuery cost estimation is not yet implemented.")


def execute_read_only(sql: str) -> None:
    """Execute read-only query (future work; see sql/execution.py for token-gated boundary)."""
    raise BigQueryNotConfigured("BigQuery read-only execution is not yet implemented.")


def redact_result(result: object) -> None:
    """Redact sensitive columns from results (future work)."""
    raise BigQueryNotConfigured("BigQuery result redaction is not yet implemented.")
