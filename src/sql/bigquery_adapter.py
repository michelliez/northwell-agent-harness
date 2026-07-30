"""BigQuery adapter: dry-run, execution, and result redaction.

Implements safe, bounded interactions with BigQuery:
- dry_run: estimate cost without executing
- execute_read_only: read-only execution with strict constraints
- estimate_cost: (future) cost forecast from dry-run
- redact_result: (future) remove sensitive columns from results
"""

from __future__ import annotations

import os

from google.cloud import bigquery
from google.cloud.exceptions import GoogleCloudError

from sql.models import DryRunResult


class BigQueryNotConfigured(RuntimeError):
    """Raised when BigQuery is not configured or credentials are missing."""


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
        BigQueryNotConfigured: if credentials cannot be found.
    """
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
        raise BigQueryNotConfigured(
            f"Failed to initialize BigQuery client: {exc}"
        ) from exc

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
                    f"{t.project}.{t.dataset_id}.{t.table_id}"
                    for t in job.referenced_tables
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


def estimate_cost(sql: str) -> None:
    """Estimate cost from dry-run (future work)."""
    raise BigQueryNotConfigured("BigQuery cost estimation is not yet implemented.")


def execute_read_only(sql: str) -> None:
    """Execute read-only query (future work; see sql/execution.py for token-gated boundary)."""
    raise BigQueryNotConfigured("BigQuery read-only execution is not yet implemented.")


def redact_result(result: object) -> None:
    """Redact sensitive columns from results (future work)."""
    raise BigQueryNotConfigured("BigQuery result redaction is not yet implemented.")
