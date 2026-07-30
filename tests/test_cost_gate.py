from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from sql.compiler import plan_to_bigquery_sql
from sql.cost_gate import (
    ApprovalTokenError,
    CostExecutionConfig,
    evaluate_cost_execution,
    require_valid_approval_token,
)
from sql.execution import execute_approved_query
from sql.models import (
    CompiledQuery,
    DryRunResult,
    PermissionScope,
    QueryPlanAST,
    SchemaColumn,
    SchemaSnapshot,
    SchemaTable,
)
from sql.planning import validate_query_plan

QUESTION = "Count events in the requested time range"
CITATION = "chunk-events"
NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)
SECRET = b"a-secure-test-key-that-is-at-least-32-bytes"


def _approved(*, with_partition_filter: bool = True):
    scope = PermissionScope(
        schema_snapshot=SchemaSnapshot(
            tables=[
                SchemaTable(
                    name="EVENT_FACT",
                    columns=[
                        SchemaColumn(
                            name="EVENT_DATE",
                            data_type="DATE",
                            safety="safe_aggregate",
                        )
                    ],
                    source_chunk_ids=[CITATION],
                )
            ],
            derived_from_chunks=[CITATION],
        ),
        required_partition_columns={"EVENT_FACT": "EVENT_DATE"},
    )
    payload = {
        "objective": QUESTION,
        "target_metric": "event_count",
        "tables": ["EVENT_FACT"],
        "aggregations": [{"function": "COUNT", "alias": "event_count"}],
        "expected_output": ["event_count"],
        "citations": [CITATION],
    }
    if with_partition_filter:
        payload["time_constraints"] = [
            {
                "column": {"table": "EVENT_FACT", "column": "EVENT_DATE"},
                "operator": "BETWEEN",
                "parameter_names": ["start_date", "end_date"],
            }
        ]
        payload["parameters"] = [
            {"name": "start_date", "type": "DATE", "value": "2026-01-01"},
            {"name": "end_date", "type": "DATE", "value": "2026-01-31"},
        ]
    plan = QueryPlanAST.model_validate(payload)
    result = validate_query_plan(QUESTION, plan, scope, {CITATION})
    assert result.approved_plan is not None, result.violations
    return result.approved_plan


def _config(max_bytes: int = 1_000) -> CostExecutionConfig:
    return CostExecutionConfig(
        max_bytes=max_bytes,
        approval_ttl_seconds=300,
        hmac_secret=SECRET,
        project="clinical-analytics",
        location="US",
    )


def _dry_run(bytes_processed: int = 900) -> DryRunResult:
    return DryRunResult(
        valid=True,
        total_bytes_processed=bytes_processed,
        project="clinical-analytics",
        location="us",
        referenced_tables=["clinical-analytics.clarity.EVENT_FACT"],
    )


def test_approves_bounded_partitioned_query_and_issues_token() -> None:
    approved = _approved()
    compiled = plan_to_bigquery_sql(approved)

    result = evaluate_cost_execution(compiled, approved, _dry_run(), _config(), now=NOW)

    assert result.allowed
    assert result.approval_token
    assert result.expires_at == NOW + timedelta(seconds=300)
    assert (
        require_valid_approval_token(
            result.approval_token,
            compiled,
            approved,
            SECRET,
            now=NOW,
            expected_max_bytes=1_000,
        )
        == 1_000
    )


def test_rejects_estimate_above_configured_ceiling() -> None:
    approved = _approved()
    result = evaluate_cost_execution(
        plan_to_bigquery_sql(approved),
        approved,
        _dry_run(1_001),
        _config(),
        now=NOW,
    )

    assert not result.allowed
    assert [item.code for item in result.violations] == ["max_bytes_exceeded"]
    assert result.approval_token is None


def test_rejects_missing_required_partition_filter() -> None:
    approved = _approved(with_partition_filter=False)

    result = evaluate_cost_execution(
        plan_to_bigquery_sql(approved), approved, _dry_run(), _config(), now=NOW
    )

    assert not result.allowed
    assert "partition_filter_required" in {item.code for item in result.violations}


def test_rejects_limit_as_cost_control() -> None:
    approved = _approved()
    original = plan_to_bigquery_sql(approved)
    compiled = original.model_copy(update={"sql": f"{original.sql}\nLIMIT 1"})

    result = evaluate_cost_execution(compiled, approved, _dry_run(), _config(), now=NOW)

    assert not result.allowed
    codes = {item.code for item in result.violations}
    assert "limit_is_not_cost_control" in codes
    assert "static_validation_required" in codes


def test_rejects_dry_run_table_scope_change() -> None:
    approved = _approved()
    dry_run = _dry_run().model_copy(
        update={"referenced_tables": ["clinical-analytics.clarity.OTHER_TABLE"]}
    )

    result = evaluate_cost_execution(
        plan_to_bigquery_sql(approved), approved, dry_run, _config(), now=NOW
    )

    assert not result.allowed
    assert "referenced_tables_mismatch" in {item.code for item in result.violations}


def test_token_rejects_tampering_expiry_and_changed_ceiling() -> None:
    approved = _approved()
    compiled = plan_to_bigquery_sql(approved)
    result = evaluate_cost_execution(compiled, approved, _dry_run(), _config(), now=NOW)
    assert result.approval_token

    changed_sql = compiled.model_copy(update={"sql": compiled.sql + "\n"})
    with pytest.raises(ApprovalTokenError, match="does not match"):
        require_valid_approval_token(
            result.approval_token, changed_sql, approved, SECRET, now=NOW
        )
    with pytest.raises(ApprovalTokenError, match="expired"):
        require_valid_approval_token(
            result.approval_token,
            compiled,
            approved,
            SECRET,
            now=NOW + timedelta(seconds=301),
        )
    with pytest.raises(ApprovalTokenError, match="byte ceiling"):
        require_valid_approval_token(
            result.approval_token,
            compiled,
            approved,
            SECRET,
            now=NOW,
            expected_max_bytes=2_000,
        )


class _FakeExecutor:
    def execute(self, query: CompiledQuery, *, maximum_bytes_billed: int) -> tuple[str, int]:
        return query.sql, maximum_bytes_billed


def test_execution_boundary_requires_and_rechecks_token() -> None:
    approved = _approved()
    compiled = plan_to_bigquery_sql(approved)
    config = _config()
    decision = evaluate_cost_execution(compiled, approved, _dry_run(), config)
    assert decision.approval_token

    with pytest.raises(ApprovalTokenError, match="required"):
        execute_approved_query(compiled, approved, None, config, _FakeExecutor())

    sql, ceiling = execute_approved_query(
        compiled,
        approved,
        decision.approval_token,
        config,
        _FakeExecutor(),
    )
    assert sql == compiled.sql
    assert ceiling == config.max_bytes


def test_config_requires_strong_signing_key() -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        replace(_config(), hmac_secret=b"short")
