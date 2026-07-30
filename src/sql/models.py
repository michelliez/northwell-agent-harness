from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

import sqlglot
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SchemaColumn(BaseModel):
    """One column's schema, with an evidence-backed safety classification."""

    name: str = Field(min_length=1)
    data_type: str | None = None
    # identifier: may appear only in COUNT/COUNT DISTINCT or identifier-to-identifier JOIN
    # sensitive: forbidden in output projections
    # safe_aggregate: no restrictions in aggregate queries
    # unknown: unresolved; may not be referenced by generated SQL
    safety: Literal["identifier", "sensitive", "safe_aggregate", "unknown"] = "unknown"
    source_evidence: str | None = None  # chunk_id where this column info came from


class SchemaTable(BaseModel):
    """One table's schema derived from retrieval evidence."""

    name: str = Field(min_length=1)
    description: str | None = None
    columns: list[SchemaColumn] = Field(default_factory=list)
    source_chunk_ids: list[str] = Field(min_length=1)


class SchemaSnapshot(BaseModel):
    """Schema evidence derived from retrieval results, used by the SQL workflow."""

    tables: list[SchemaTable] = Field(default_factory=list)
    index_version: str = "unknown"
    derived_from_chunks: list[str] = Field(default_factory=list)

    @property
    def tables_by_name(self) -> dict[str, SchemaTable]:
        return {t.name.lower(): t for t in self.tables}

    @property
    def known_table_names(self) -> frozenset[str]:
        return frozenset(t.name.lower() for t in self.tables)

    def has_unknown_safety(self) -> bool:
        return any(col.safety == "unknown" for t in self.tables for col in t.columns)


class CatalogRef(BaseModel):
    """A schema identifier proposed by the planner, never a free-form SQL fragment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str = Field(min_length=1)
    column: str = Field(min_length=1)


class PlannedFilter(BaseModel):
    """A parameterized predicate; literal values never appear in generated SQL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    column: CatalogRef
    operator: Literal["=", "!=", "<", "<=", ">", ">=", "IN", "BETWEEN"]
    parameter_names: list[
        Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
    ] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def validate_parameter_arity(self) -> Self:
        expected = 2 if self.operator == "BETWEEN" else 1
        if len(self.parameter_names) != expected:
            raise ValueError(f"{self.operator} requires {expected} parameter name(s)")
        return self


class PlannedJoin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left: CatalogRef
    right: CatalogRef
    join_type: Literal["INNER", "LEFT"]


class PlannedAggregation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    function: Literal["COUNT", "COUNT_DISTINCT", "SUM", "AVG", "MIN", "MAX"]
    column: CatalogRef | None = None
    alias: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")

    @model_validator(mode="after")
    def validate_count_star(self) -> Self:
        if self.column is None and self.function != "COUNT":
            raise ValueError("only COUNT may omit its column")
        return self


class PlannedParameter(BaseModel):
    """A typed BigQuery parameter kept separate from SQL text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
    type: Literal["STRING", "INT64", "FLOAT64", "BOOL", "DATE", "DATETIME", "TIMESTAMP"]
    value: str | int | float | bool | list[str] | list[int] | list[float] | list[bool]


class QueryPlanAST(BaseModel):
    """Untrusted structured plan proposed by Claude; contains no SQL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    objective: str = Field(min_length=1)
    target_metric: str = Field(min_length=1)
    tables: list[str] = Field(min_length=1, max_length=4)
    columns: list[CatalogRef] = Field(default_factory=list)
    filters: list[PlannedFilter] = Field(default_factory=list)
    joins: list[PlannedJoin] = Field(default_factory=list, max_length=3)
    aggregations: list[PlannedAggregation] = Field(min_length=1)
    time_constraints: list[PlannedFilter] = Field(default_factory=list)
    groupings: list[CatalogRef] = Field(default_factory=list)
    expected_output: list[str] = Field(min_length=1)
    citations: list[str] = Field(min_length=1)
    parameters: list[PlannedParameter] = Field(default_factory=list)
    requires_row_level_access: bool = False


class PermissionScope(BaseModel):
    """Host-owned query authority. Models may consume but never create this."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_snapshot: SchemaSnapshot
    aggregate_only: bool = True
    allow_row_level: bool = False
    allow_sensitive: bool = False
    max_tables: int = Field(default=4, ge=1, le=20)
    max_joins: int = Field(default=3, ge=0, le=10)
    required_partition_columns: dict[str, str] = Field(default_factory=dict)


class ApprovedQueryPlan(BaseModel):
    """A proposal accepted by deterministic validation and bound to its scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: QueryPlanAST
    permission_scope: PermissionScope
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    safety_notes: list[str] = Field(default_factory=list)


class PlanViolation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    evidence: dict = Field(default_factory=dict)


class PlanValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    approved_plan: ApprovedQueryPlan | None = None
    violations: list[PlanViolation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result_state(self) -> Self:
        if self.allowed != (self.approved_plan is not None):
            raise ValueError("allowed must match presence of approved_plan")
        if self.allowed and self.violations:
            raise ValueError("approved plans cannot contain violations")
        if not self.allowed and not self.violations:
            raise ValueError("rejected plans require at least one violation")
        return self


class CompiledQuery(BaseModel):
    """SQL text plus separately-bound values produced from an approved plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sql: str = Field(min_length=1)
    parameters: list[PlannedParameter] = Field(default_factory=list)
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    compiler_version: str
    source: Literal["deterministic", "claude_fallback", "claude_repair"]


class DryRunResult(BaseModel):
    """Trusted evidence returned by a BigQuery dry-run adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    total_bytes_processed: int | None = Field(default=None, ge=0)
    project: str | None = None
    location: str | None = None
    referenced_tables: list[str] = Field(default_factory=list)
    error: str | None = None

    @model_validator(mode="after")
    def validate_result_state(self) -> Self:
        if self.valid:
            if self.total_bytes_processed is None or self.error is not None:
                raise ValueError("valid dry runs require bytes and cannot contain an error")
        elif not self.error:
            raise ValueError("invalid dry runs require an error")
        return self


class CostGateViolation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    evidence: dict = Field(default_factory=dict)


class CostGateResult(BaseModel):
    """Decision made from an approved plan, validated SQL, and dry-run evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    total_bytes_processed: int | None = Field(default=None, ge=0)
    max_bytes: int = Field(gt=0)
    expires_at: datetime | None = None
    approval_token: str | None = None
    violations: list[CostGateViolation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_decision_state(self) -> Self:
        if self.allowed:
            if not self.approval_token or self.expires_at is None or self.violations:
                raise ValueError("allowed decisions require a token and expiry")
        elif self.approval_token is not None or self.expires_at is not None:
            raise ValueError("rejected decisions cannot issue approval credentials")
        elif not self.violations:
            raise ValueError("rejected decisions require at least one violation")
        return self


class RepairAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt: int = Field(ge=1)
    input_sql_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_sql_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    error_code: str


class SqlGenerationResult(BaseModel):
    sql: str | None = None
    tables: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    refused: bool = False
    reason: str | None = None
    source: str = "claude_sql_generation"

    @model_validator(mode="after")
    def validate_result_state(self) -> SqlGenerationResult:
        if self.refused:
            if self.sql is not None or self.tables or not self.reason:
                raise ValueError("refusals require a reason and cannot contain SQL or tables")
        elif self.sql is None:
            if self.tables or self.reason != "unsupported_or_ambiguous_request":
                raise ValueError("unsupported results require the canonical reason and no tables")
        elif not self.tables or self.reason is not None:
            raise ValueError("generated SQL requires tables and cannot contain a reason")
        return self


VALIDATOR_VERSION = "sqlglot_ast_v2"


class SqlViolation(BaseModel):
    code: str
    message: str
    evidence: dict = Field(default_factory=dict)


class SqlValidationResult(BaseModel):
    allowed: bool
    disclosure_status: Literal["not_evaluated", "approved", "suppressed", "denied"] = (
        "not_evaluated"
    )
    requires_authorized_execution: bool = True
    reason: str | None = None
    normalized_sql: str | None = None
    tables: list[str] = Field(default_factory=list)
    referenced_tables: list[str] = Field(default_factory=list)
    referenced_columns: list[str] = Field(default_factory=list)
    declared_tables: list[str] = Field(default_factory=list)
    statement_type: str | None = None
    violations: list[SqlViolation] = Field(default_factory=list)
    validator_version: str = VALIDATOR_VERSION
    sqlglot_version: str = sqlglot.__version__
    notes: list[str] = Field(default_factory=list)
    source: str = "deterministic_sql_validation"
    is_repairable: bool = False
    repair_hint: str | None = None

    @model_validator(mode="after")
    def enforce_allowed_result_contract(self) -> SqlValidationResult:
        if self.allowed and (not self.normalized_sql or self.violations):
            raise ValueError("allowed results require normalized SQL and no violations")
        if not self.allowed and (self.normalized_sql is not None or not self.violations):
            raise ValueError("blocked results require violations and no SQL")
        return self
