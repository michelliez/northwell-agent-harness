"""Small host-owned registry for bounded in-process retrieval tools."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_host.budget import ExecutionBudget
from retrieval.search import fetch_chunk_by_id, open_connection, search_ranked_chunks

ExplorationIntent = Literal["table_discovery", "schema_lookup", "aggregate_definition"]


class _StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindTableDocArgs(_StrictArgs):
    table_name: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=5, ge=1, le=10)


class GetDocSectionArgs(_StrictArgs):
    document_name: str = Field(min_length=1, max_length=256)
    section_name: str = Field(min_length=1, max_length=256)
    limit: int = Field(default=5, ge=1, le=10)


class SearchColumnsArgs(_StrictArgs):
    query: str = Field(min_length=1, max_length=1_000)
    limit: int = Field(default=5, ge=1, le=10)


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "find_table_doc",
        "description": "Find documentation chunks for an exact Clarity table name.",
        "input_schema": FindTableDocArgs.model_json_schema(),
    },
    {
        "name": "get_doc_section",
        "description": "Retrieve a bounded named section from a documentation page.",
        "input_schema": GetDocSectionArgs.model_json_schema(),
    },
    {
        "name": "search_columns",
        "description": "Search approved column documentation for a concept or column name.",
        "input_schema": SearchColumnsArgs.model_json_schema(),
    },
]

_ARG_MODELS: dict[str, type[_StrictArgs]] = {
    "find_table_doc": FindTableDocArgs,
    "get_doc_section": GetDocSectionArgs,
    "search_columns": SearchColumnsArgs,
}

_ALLOWED_TOOLS: dict[str, frozenset[str]] = {
    "table_discovery": frozenset({"find_table_doc", "search_columns"}),
    "schema_lookup": frozenset({"find_table_doc", "get_doc_section", "search_columns"}),
    "aggregate_definition": frozenset({"find_table_doc", "get_doc_section", "search_columns"}),
}


def tools_for_intent(intent: str) -> list[dict[str, Any]]:
    allowed = _ALLOWED_TOOLS.get(intent, frozenset())
    return [tool for tool in TOOL_DEFINITIONS if tool["name"] in allowed]


def execute_retrieval_tool(
    intent: str,
    name: str,
    arguments: Any,
    *,
    db_path: Path,
    budget: ExecutionBudget,
) -> dict[str, Any]:
    """Validate authority and arguments again at execution time."""
    if name not in _ALLOWED_TOOLS.get(intent, frozenset()):
        raise PermissionError(f"tool {name!r} is not allowed for intent {intent!r}")
    model = _ARG_MODELS.get(name)
    if model is None:
        raise ValueError(f"unknown retrieval tool: {name}")

    validated = model.model_validate(arguments)
    payload = validated.model_dump()
    budget.reserve_tool_call(name, payload)

    conn = open_connection(db_path)
    try:
        if name == "find_table_doc":
            result = _find_table_doc(conn, FindTableDocArgs.model_validate(payload))
        elif name == "get_doc_section":
            result = _get_doc_section(conn, GetDocSectionArgs.model_validate(payload))
        else:
            result = _search_columns(conn, SearchColumnsArgs.model_validate(payload))
    finally:
        conn.close()

    budget.accept_tool_result(result)
    return result


def _find_table_doc(conn: sqlite3.Connection, args: FindTableDocArgs) -> dict[str, Any]:
    normalized = args.table_name.upper().removesuffix(".HTML")
    rows = conn.execute(
        """
        SELECT c.chunk_id
        FROM docs d
        JOIN chunks c ON c.doc_id = d.doc_id
        WHERE upper(d.source_path) = ?
           OR upper(d.source_path) LIKE ?
           OR upper(d.title) LIKE ?
        ORDER BY c.chunk_index
        LIMIT ?
        """,
        (f"{normalized}.HTML", f"%/{normalized}.HTML", f"{normalized} - %", args.limit),
    ).fetchall()
    return {"chunks": _full_chunks(conn, [str(row["chunk_id"]) for row in rows])}


def _get_doc_section(conn: sqlite3.Connection, args: GetDocSectionArgs) -> dict[str, Any]:
    document = args.document_name.upper().removesuffix(".HTML")
    section = f"%{args.section_name.casefold()}%"
    rows = conn.execute(
        """
        SELECT c.chunk_id
        FROM docs d
        JOIN chunks c ON c.doc_id = d.doc_id
        WHERE (
            upper(d.source_path) = ?
            OR upper(d.source_path) LIKE ?
            OR upper(d.title) LIKE ?
        )
          AND lower(coalesce(c.heading_path, '')) LIKE ?
        ORDER BY c.chunk_index
        LIMIT ?
        """,
        (f"{document}.HTML", f"%/{document}.HTML", f"{document} - %", section, args.limit),
    ).fetchall()
    return {"chunks": _full_chunks(conn, [str(row["chunk_id"]) for row in rows])}


def _search_columns(conn: sqlite3.Connection, args: SearchColumnsArgs) -> dict[str, Any]:
    cur = conn.cursor()
    ranked = search_ranked_chunks(cur, query=args.query, top_k=args.limit * 3)
    chunk_ids: list[str] = []
    for hit in ranked:
        row = conn.execute(
            "SELECT category FROM chunks WHERE chunk_id = ?",
            (str(hit["chunk_id"]),),
        ).fetchone()
        if row is not None and row["category"] == "column_info":
            chunk_ids.append(str(hit["chunk_id"]))
        if len(chunk_ids) == args.limit:
            break
    return {"chunks": _full_chunks(conn, chunk_ids)}


def _full_chunks(conn: sqlite3.Connection, chunk_ids: list[str]) -> list[dict[str, Any]]:
    cur = conn.cursor()
    chunks: list[dict[str, Any]] = []
    for rank, chunk_id in enumerate(chunk_ids, start=1):
        chunk = fetch_chunk_by_id(cur, chunk_id)
        if chunk is not None:
            chunk["rank"] = rank
            chunks.append(chunk)
    return chunks
