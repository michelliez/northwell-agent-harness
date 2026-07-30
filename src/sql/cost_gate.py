"""Deterministic cost authorization and short-lived execution credentials."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from sql.models import (
    ApprovedQueryPlan,
    CompiledQuery,
    CostGateResult,
    CostGateViolation,
    DryRunResult,
)
from sql.validation import validate_sql

TOKEN_VERSION = "v1"


@dataclass(frozen=True)
class CostExecutionConfig:
    """Host-owned limits and signing material; never supplied by a model."""

    max_bytes: int
    approval_ttl_seconds: int
    hmac_secret: bytes
    project: str | None = None
    location: str | None = None

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if not 1 <= self.approval_ttl_seconds <= 3600:
            raise ValueError("approval_ttl_seconds must be between 1 and 3600")
        if len(self.hmac_secret) < 32:
            raise ValueError("hmac_secret must contain at least 32 bytes")


class ApprovalTokenError(PermissionError):
    """The execution credential is absent, malformed, expired, or mismatched."""


def cost_execution_config_from_env() -> CostExecutionConfig:
    """Load trusted cost settings, failing closed on missing/invalid values."""
    try:
        max_bytes = int(os.environ["BIGQUERY_MAX_BYTES_PROCESSED"])
        ttl = int(os.getenv("BIGQUERY_APPROVAL_TOKEN_TTL_SECONDS", "300"))
    except KeyError as exc:
        raise RuntimeError("Set BIGQUERY_MAX_BYTES_PROCESSED before cost approval.") from exc
    except ValueError as exc:
        raise RuntimeError("BigQuery cost limits must be integers.") from exc

    secret = os.getenv("BIGQUERY_APPROVAL_HMAC_SECRET", "").encode()
    if not secret:
        raise RuntimeError("Set BIGQUERY_APPROVAL_HMAC_SECRET before cost approval.")
    try:
        return CostExecutionConfig(
            max_bytes=max_bytes,
            approval_ttl_seconds=ttl,
            hmac_secret=secret,
            project=os.getenv("BIGQUERY_PROJECT") or None,
            location=os.getenv("BIGQUERY_LOCATION") or None,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def evaluate_cost_execution(
    compiled: CompiledQuery,
    approved: ApprovedQueryPlan,
    dry_run: DryRunResult,
    config: CostExecutionConfig,
    *,
    now: datetime | None = None,
) -> CostGateResult:
    """Authorize only a bounded scan matching the exact approved query."""
    violations: list[CostGateViolation] = []

    if compiled.plan_hash != approved.plan_hash:
        violations.append(_violation("plan_hash_mismatch", "SQL is not bound to this plan."))
    static_validation = validate_sql(
        compiled.sql,
        approved.plan.tables,
        approved.permission_scope.schema_snapshot,
        approved,
    )
    if not static_validation.allowed:
        violations.append(
            _violation(
                "static_validation_required",
                "SQL must pass exact ApprovedQueryPlan validation before cost approval.",
                reason=static_validation.reason,
            )
        )
    if not dry_run.valid:
        violations.append(
            _violation(
                "dry_run_failed", "BigQuery did not validate the query.", error=dry_run.error
            )
        )
    if (
        dry_run.total_bytes_processed is not None
        and dry_run.total_bytes_processed > config.max_bytes
    ):
        violations.append(
            _violation(
                "max_bytes_exceeded",
                "The dry-run estimate exceeds the configured byte ceiling.",
                estimated_bytes=dry_run.total_bytes_processed,
                max_bytes=config.max_bytes,
            )
        )
    if config.project and dry_run.project != config.project:
        violations.append(
            _violation(
                "project_mismatch",
                "Dry-run project does not match the configured project.",
                expected=config.project,
                actual=dry_run.project,
            )
        )
    if config.location and (dry_run.location or "").casefold() != config.location.casefold():
        violations.append(
            _violation(
                "location_mismatch",
                "Dry-run location does not match the configured location.",
                expected=config.location,
                actual=dry_run.location,
            )
        )

    planned_tables = {_short_identifier(table) for table in approved.plan.tables}
    dry_run_tables = {_short_identifier(table) for table in dry_run.referenced_tables}
    if dry_run_tables != planned_tables:
        violations.append(
            _violation(
                "referenced_tables_mismatch",
                "Dry-run tables do not exactly match the approved plan.",
                planned=sorted(planned_tables),
                actual=sorted(dry_run_tables),
            )
        )

    try:
        expression = sqlglot.parse_one(compiled.sql, read="bigquery")
    except ParseError:
        violations.append(_violation("sql_parse_failed", "Cost gate could not parse the SQL."))
        expression = None

    if expression is not None and expression.find(exp.Limit):
        violations.append(
            _violation(
                "limit_is_not_cost_control",
                "LIMIT cannot be used as evidence that a BigQuery scan is inexpensive.",
            )
        )

    required_partitions = {
        _short_identifier(table): column.casefold()
        for table, column in approved.permission_scope.required_partition_columns.items()
    }
    approved_predicates = [
        *approved.plan.filters,
        *approved.plan.time_constraints,
    ]
    predicate_refs = {
        (_short_identifier(item.column.table), item.column.column.casefold())
        for item in approved_predicates
    }
    for table, column in required_partitions.items():
        if (table, column) not in predicate_refs:
            violations.append(
                _violation(
                    "partition_filter_required",
                    "A required partition column is not constrained by the approved plan.",
                    table=table,
                    column=column,
                )
            )

    if violations:
        return CostGateResult(
            allowed=False,
            total_bytes_processed=dry_run.total_bytes_processed,
            max_bytes=config.max_bytes,
            violations=violations,
        )

    issued_at = _utc(now)
    expires_at = issued_at + timedelta(seconds=config.approval_ttl_seconds)
    token = _issue_token(
        compiled,
        approved,
        config.max_bytes,
        expires_at,
        config.hmac_secret,
    )
    return CostGateResult(
        allowed=True,
        total_bytes_processed=dry_run.total_bytes_processed,
        max_bytes=config.max_bytes,
        expires_at=expires_at,
        approval_token=token,
    )


def require_valid_approval_token(
    token: str | None,
    compiled: CompiledQuery,
    approved: ApprovedQueryPlan,
    secret: bytes,
    *,
    now: datetime | None = None,
    expected_max_bytes: int | None = None,
) -> int:
    """Verify the token against current SQL, plan, scope, ceiling, and time."""
    if not token:
        raise ApprovalTokenError("an approval token is required")
    try:
        version, raw_expiry, raw_max_bytes, signature = token.split(".")
        expiry_epoch = int(raw_expiry)
        max_bytes = int(raw_max_bytes)
    except (ValueError, TypeError) as exc:
        raise ApprovalTokenError("approval token is malformed") from exc
    if version != TOKEN_VERSION or max_bytes <= 0:
        raise ApprovalTokenError("approval token is malformed")
    if expected_max_bytes is not None and max_bytes != expected_max_bytes:
        raise ApprovalTokenError("approval token byte ceiling does not match execution config")
    if compiled.plan_hash != approved.plan_hash:
        raise ApprovalTokenError("compiled SQL is not bound to the approved plan")

    expires_at = datetime.fromtimestamp(expiry_epoch, tz=UTC)
    if _utc(now) >= expires_at:
        raise ApprovalTokenError("approval token has expired")

    expected = _signature(
        compiled,
        approved,
        max_bytes,
        expiry_epoch,
        secret,
    )
    if not hmac.compare_digest(signature, expected):
        raise ApprovalTokenError("approval token does not match this execution request")
    return max_bytes


def _issue_token(
    compiled: CompiledQuery,
    approved: ApprovedQueryPlan,
    max_bytes: int,
    expires_at: datetime,
    secret: bytes,
) -> str:
    expiry_epoch = int(expires_at.timestamp())
    signature = _signature(compiled, approved, max_bytes, expiry_epoch, secret)
    return f"{TOKEN_VERSION}.{expiry_epoch}.{max_bytes}.{signature}"


def _signature(
    compiled: CompiledQuery,
    approved: ApprovedQueryPlan,
    max_bytes: int,
    expiry_epoch: int,
    secret: bytes,
) -> str:
    payload = json.dumps(
        {
            "sql_hash": hashlib.sha256(compiled.sql.encode()).hexdigest(),
            "plan_hash": approved.plan_hash,
            "scope_hash": approved.scope_hash,
            "max_bytes": max_bytes,
            "expiry": expiry_epoch,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = hmac.new(secret, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(UTC)


def _short_identifier(value: str) -> str:
    return value.strip("`").rsplit(".", 1)[-1].casefold()


def _violation(code: str, message: str, **evidence: object) -> CostGateViolation:
    return CostGateViolation(code=code, message=message, evidence=evidence)
