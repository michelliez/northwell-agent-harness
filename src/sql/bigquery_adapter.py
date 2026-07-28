"""Explicitly disabled BigQuery adapter stub.

BigQuery dry-run, cost approval, read-only execution, and result redaction are
future work. These entry points exist so the graph can reference them as
named fail-closed stubs and so tests can verify they cannot be reached through
model output.
"""

from __future__ import annotations


class BigQueryNotConfigured(RuntimeError):
    """Raised by all BigQuery adapter functions; execution is not configured."""


def dry_run(sql: str) -> None:
    raise BigQueryNotConfigured("BigQuery dry-run is not configured in this environment.")


def estimate_cost(sql: str) -> None:
    raise BigQueryNotConfigured("BigQuery cost estimation is not configured.")


def execute_read_only(sql: str) -> None:
    raise BigQueryNotConfigured("BigQuery read-only execution is not configured.")


def redact_result(result: object) -> None:
    raise BigQueryNotConfigured("BigQuery result redaction is not configured.")
