from __future__ import annotations

import re

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from harness_spike.mcp_servers.data_catalog import TABLES


mcp = FastMCP("sql_validation")


class ValidateSqlArgs(BaseModel):
    sql: str = Field(min_length=1, max_length=10_000)
    tables: list[str] = Field(default_factory=list)


class SqlValidationResult(BaseModel):
    allowed: bool
    reason: str | None = None
    normalized_sql: str | None = None
    tables: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    source: str = "deterministic_sql_validation"
    is_dummy: bool = True


FORBIDDEN_SQL_TERMS = {
    "alter",
    "begin",
    "call",
    "commit",
    "connection",
    "copy",
    "create",
    "declare",
    "delete",
    "drop",
    "exec",
    "execute",
    "export",
    "external",
    "grant",
    "immediate",
    "insert",
    "merge",
    "remote",
    "rollback",
    "truncate",
    "update",
}

IDENTIFIER_COLUMNS = {"patient_id", "encounter_id", "appointment_id"}


@mcp.tool
def validate_sql(sql: str, tables: list[str] | None = None) -> dict[str, object]:
    """
    Validate generated SQL before it can be returned or executed.

    This node is deterministic and only allows simple read-only SQL over the
    mock catalog tables.
    """
    args = ValidateSqlArgs(sql=sql.strip(), tables=tables or [])
    normalized_sql = args.sql.strip()
    lowered = normalized_sql.lower()
    first_word = lowered.split(maxsplit=1)[0] if lowered else ""
    terms = {part.strip("(),;") for part in lowered.replace("\n", " ").split()}

    if _contains_forbidden_sql_term(lowered, terms):
        return _blocked("unsafe_sql_operation", args.tables)
    if first_word not in {"select", "with"}:
        return _blocked("non_read_only_sql", args.tables)
    if any(column in lowered for column in IDENTIFIER_COLUMNS):
        return _blocked("identifier_column_selected", args.tables)
    if re.search(r"\bselect\s+\*", lowered):
        return _blocked("select_star_sql", args.tables)
    if re.search(r"\b(from|join)\s+[`\"]?[\w.\-]*\*", lowered):
        return _blocked("wildcard_table_scan", args.tables)
    if ";" in normalized_sql.rstrip(";"):
        return _blocked("multiple_statement_sql", args.tables)
    if _uses_unknown_table(args.tables):
        return _blocked("unknown_table", args.tables)

    return SqlValidationResult(
        allowed=True,
        normalized_sql=normalized_sql,
        tables=args.tables,
        notes=["SQL passed deterministic mock-catalog validation."],
    ).model_dump()


def _contains_forbidden_sql_term(sql: str, terms: set[str]) -> bool:
    if any(term in terms for term in FORBIDDEN_SQL_TERMS):
        return True
    return any(
        re.search(rf"\b{re.escape(term)}(?:\b|_)", sql)
        for term in FORBIDDEN_SQL_TERMS
    )


def _uses_unknown_table(tables: list[str]) -> bool:
    return any(table not in TABLES for table in tables)


def _blocked(reason: str, tables: list[str]) -> dict[str, object]:
    return SqlValidationResult(
        allowed=False,
        reason=reason,
        normalized_sql=None,
        tables=tables,
        notes=["Generated SQL failed the validation node."],
    ).model_dump()


def main() -> None:
    mcp.run(transport="http", host="localhost", port=8004, path="/mcp")


if __name__ == "__main__":
    main()
