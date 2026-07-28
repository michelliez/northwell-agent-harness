"""Retrieval client: direct SQLite search, schema evidence extraction."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from agent_host.budget import ExecutionBudget
from retrieval import search


class RetrievedChunk(BaseModel):
    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    heading_path: str | None = None
    category: str | None = None
    text: str = Field(min_length=1)
    rank: int = Field(ge=1)
    score: float | None = None


class RetrievalResult(BaseModel):
    query: str = Field(min_length=1)
    chunks: list[RetrievedChunk]
    index_version: str = Field(min_length=1)


# Import here to avoid circular imports in nodes that import from retrieval
from sql.models import SchemaColumn, SchemaSnapshot, SchemaTable  # noqa: E402


def retrieve_documentation(
    query: str,
    db_path: Path,
    *,
    budget: ExecutionBudget,
    top_k: int = 5,
) -> RetrievalResult:
    """Search the SQLite RAG index and return bounded, typed chunks."""
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    bounded_top_k = budget.bound_retrieval_count(top_k)

    result = search.retrieve_documentation_context(query, db_path, top_k=bounded_top_k)

    chunks: list[RetrievedChunk] = []
    for fallback_rank, chunk in enumerate(result.get("chunks", [])[:bounded_top_k], start=1):
        if not isinstance(chunk, dict):
            continue
        chunks.append(
            RetrievedChunk(
                chunk_id=chunk["chunk_id"],
                document_id=chunk["doc_id"],
                source_path=chunk["source_path"],
                heading_path=chunk.get("heading_path"),
                category=chunk.get("category"),
                text=chunk["text"],
                rank=chunk.get("rank", fallback_rank),
                score=chunk.get("score"),
            )
        )

    return RetrievalResult(
        query=query,
        chunks=chunks,
        index_version=str(result.get("index_version", "unknown")),
    )


def resolve_schema_evidence(retrieval: RetrievalResult) -> SchemaSnapshot:
    """Build a SchemaSnapshot from retrieved documentation chunks.

    Extracts table and column information from column_info category chunks.
    Columns whose safety cannot be determined from evidence are marked
    'unknown', which will trigger clarification before SQL generation.
    """
    tables_by_name: dict[str, dict] = {}

    for chunk in retrieval.chunks:
        if chunk.category not in ("column_info", "table_data", "table_generic", "metadata"):
            continue

        table_name = _extract_table_name(chunk.source_path, chunk.heading_path)
        if not table_name:
            continue

        if table_name not in tables_by_name:
            tables_by_name[table_name] = {
                "name": table_name,
                "description": None,
                "columns": {},
                "source_chunk_ids": [],
            }

        tables_by_name[table_name]["source_chunk_ids"].append(chunk.chunk_id)

        if chunk.category == "column_info":
            col_name = _extract_column_name(chunk.heading_path)
            if col_name:
                safety = _infer_safety(col_name, chunk.text)
                col_type = _extract_col_type(chunk.text)
                tables_by_name[table_name]["columns"][col_name] = {
                    "name": col_name,
                    "data_type": col_type,
                    "safety": safety,
                    "source_evidence": chunk.chunk_id,
                }
        elif chunk.category in ("table_data", "table_generic", "metadata"):
            if not tables_by_name[table_name]["description"]:
                tables_by_name[table_name]["description"] = chunk.text[:200]

    schema_tables = [
        SchemaTable(
            name=info["name"],
            description=info["description"],
            columns=[SchemaColumn(**col_info) for col_info in info["columns"].values()],
            source_chunk_ids=info["source_chunk_ids"],
        )
        for info in tables_by_name.values()
    ]

    return SchemaSnapshot(
        tables=schema_tables,
        index_version=retrieval.index_version,
        derived_from_chunks=[c.chunk_id for c in retrieval.chunks],
    )


def _extract_table_name(source_path: str, heading_path: str | None) -> str | None:
    """Extract a table name from source_path (e.g. 'CLARITY_ADT.html' → 'CLARITY_ADT')."""
    import os

    basename = os.path.basename(source_path)
    name = basename.replace(".html", "").replace(".HTML", "").replace("-", "_")
    # Filter out generic page names
    if not name or name.upper() in {"INDEX", "HOME", "OVERVIEW"}:
        return None
    return name.upper()


def _extract_column_name(heading_path: str | None) -> str | None:
    """Extract column name from heading_path like 'Column Information > PAT_ID'."""
    if not heading_path:
        return None
    parts = [p.strip() for p in heading_path.split(">")]
    if len(parts) >= 2:
        return parts[-1].strip().upper()
    return None


# Column-name suffixes that may promote a column to safe_aggregate. This is a
# closed allowlist over the schema identifier, which is structural metadata from
# the document heading path -- not prose an author or attacker can edit freely.
_SAFE_AGGREGATE_SUFFIXES = re.compile(
    r"_(DATE|DATETIME|STATUS|TYPE|FLAG|CODE|COUNT|QTY|AMOUNT|YEAR|MONTH|DAY|DEPT)$"
)

# Column-name patterns that always restrict, regardless of what the prose says.
_IDENTIFIER_NAME = re.compile(r"(^|_)(ID|MRN|CSN|EPI|ACCT|ACCOUNT)$|_ID$")
_SENSITIVE_NAME = re.compile(
    r"(^|_)(SSN|DOB|BIRTH|NAME|FNAME|LNAME|ADDR|ADDRESS|PHONE|EMAIL|ZIP|POSTAL)($|_)"
)

# Prose markers that restrict. Text may only ever narrow permission.
_SENSITIVE_TEXT_MARKERS = (
    "sensitive",
    "phi",
    "protected health",
    "do not expose",
    "identifiable",
    "restricted",
)


def _infer_safety(col_name: str, text: str) -> str:
    """Classify one column's safety, deny-by-default.

    Retrieved documentation is untrusted content. It may only *narrow*
    permission, never widen it: prose can restrict a column to ``sensitive``,
    but promotion to ``safe_aggregate`` comes solely from a closed allowlist of
    suffixes on the column identifier. Anything unrecognized stays ``unknown``,
    which ``plan_safety_node`` and ``sql.validation`` both treat as blocking.

    A column description that merely contains the word "count" or "date" is
    therefore no longer sufficient to make that column safe to project.
    """
    name_upper = col_name.upper()
    text_lower = text.lower()

    # Restrictions first, and they win. Name-based rules are checked before
    # prose so that a mislabelled description cannot downgrade a known
    # identifier to something weaker.
    if _IDENTIFIER_NAME.search(name_upper):
        return "identifier"
    if _SENSITIVE_NAME.search(name_upper):
        return "sensitive"
    if any(marker in text_lower for marker in _SENSITIVE_TEXT_MARKERS):
        return "sensitive"

    # Promotion is allowlist-only and never reads the prose.
    if _SAFE_AGGREGATE_SUFFIXES.search(name_upper):
        return "safe_aggregate"

    return "unknown"


def _extract_col_type(text: str) -> str | None:
    """Try to extract SQL type from chunk text."""
    for candidate in (
        "VARCHAR",
        "STRING",
        "INTEGER",
        "INT64",
        "DATE",
        "DATETIME",
        "TIMESTAMP",
        "FLOAT",
        "FLOAT64",
        "BOOLEAN",
        "BOOL",
        "NUMERIC",
    ):
        if candidate.lower() in text.lower():
            return candidate
    return None
