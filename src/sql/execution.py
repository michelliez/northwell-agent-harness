"""Token-gated boundary for future read-only BigQuery execution."""

from __future__ import annotations

from typing import Protocol

from sql.cost_gate import CostExecutionConfig, require_valid_approval_token
from sql.models import ApprovedQueryPlan, CompiledQuery


class ReadOnlyExecutor[ResultT](Protocol):
    def execute(
        self,
        query: CompiledQuery,
        *,
        maximum_bytes_billed: int,
    ) -> ResultT: ...


def execute_approved_query[ResultT](
    compiled: CompiledQuery,
    approved: ApprovedQueryPlan,
    approval_token: str | None,
    config: CostExecutionConfig,
    executor: ReadOnlyExecutor[ResultT] | None = None,
) -> ResultT:
    """Execute only after revalidating a credential bound to this exact request."""
    max_bytes = require_valid_approval_token(
        approval_token,
        compiled,
        approved,
        config.hmac_secret,
        expected_max_bytes=config.max_bytes,
    )
    if executor is None:
        from sql.bigquery_adapter import BigQueryNotConfigured

        raise BigQueryNotConfigured("BigQuery read-only execution is not configured.")
    return executor.execute(compiled, maximum_bytes_billed=max_bytes)
