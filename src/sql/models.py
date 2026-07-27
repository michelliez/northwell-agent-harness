from __future__ import annotations

from typing import Literal

import sqlglot
from pydantic import BaseModel, Field, model_validator


class SchemaColumn(BaseModel):
    """One column's schema, with an evidence-backed safety classification."""

    name: str = Field(min_length=1)
    data_type: str | None = None
    # identifier: may appear only in COUNT/COUNT DISTINCT or identifier-to-identifier JOIN
    # sensitive: forbidden in output projections
    # safe_aggregate: no restrictions in aggregate queries
    # unknown: unresolved; causes clarification before SQL generation
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


class QueryPlan(BaseModel):
    """Minimal evidence-backed query plan built from retrieved documentation."""

    tables: list[str] = Field(default_factory=list)
    purpose: str = ""
    schema_snapshot: SchemaSnapshot = Field(default_factory=SchemaSnapshot)
    safety_notes: list[str] = Field(default_factory=list)


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
