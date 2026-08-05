"""Typed, bounded access to the parent-linked retrieval hierarchy."""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NodeType = Literal["document", "section", "leaf"]
MAX_NAVIGATION_NODES = 25


class HierarchyNode(BaseModel):
    """One document, semantic section, or existing retrieval chunk."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    parent_id: str | None = None
    depth: int = Field(ge=0)
    position: int = Field(ge=0)
    node_type: NodeType
    title: str = Field(min_length=1)
    heading_path: list[str] = Field(min_length=1)
    text: str
    summary: str
    token_count: int = Field(ge=1)
    chunk_id: str | None = None

    @field_validator("heading_path")
    @classmethod
    def validate_heading_path(cls, value: list[str]) -> list[str]:
        if any(not part or part != part.strip() for part in value):
            raise ValueError("heading_path entries must be non-empty and trimmed")
        return value


def _bounded_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_NAVIGATION_NODES:
        raise ValueError(f"limit must be between 1 and {MAX_NAVIGATION_NODES}")
    return limit


def _node_from_row(row: sqlite3.Row) -> HierarchyNode:
    return HierarchyNode(
        node_id=row["node_id"],
        document_id=row["document_id"],
        parent_id=row["parent_id"],
        depth=row["depth"],
        position=row["position"],
        node_type=row["node_type"],
        title=row["title"],
        heading_path=json.loads(row["heading_path"]),
        text=row["text"],
        summary=row["summary"],
        token_count=row["token_count"],
        chunk_id=row["chunk_id"],
    )


def get_node(conn: sqlite3.Connection, node_id: str) -> HierarchyNode | None:
    """Return one node by its opaque ID."""
    if not node_id:
        raise ValueError("node_id must not be empty")
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
        return _node_from_row(row) if row is not None else None
    finally:
        conn.row_factory = previous_factory


def get_parent(conn: sqlite3.Connection, node_id: str) -> HierarchyNode | None:
    """Return the immediate parent without crossing document boundaries."""
    node = get_node(conn, node_id)
    if node is None or node.parent_id is None:
        return None
    parent = get_node(conn, node.parent_id)
    if parent is None or parent.document_id != node.document_id:
        return None
    return parent


def get_children(conn: sqlite3.Connection, node_id: str, *, limit: int = 10) -> list[HierarchyNode]:
    """Return immediate children in source order, subject to a hard bound."""
    bounded = _bounded_limit(limit)
    parent = get_node(conn, node_id)
    if parent is None:
        return []
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT * FROM nodes
               WHERE parent_id = ? AND document_id = ?
               ORDER BY position, node_id LIMIT ?""",
            (node_id, parent.document_id, bounded),
        ).fetchall()
        return [_node_from_row(row) for row in rows]
    finally:
        conn.row_factory = previous_factory


def get_siblings(conn: sqlite3.Connection, node_id: str, *, limit: int = 10) -> list[HierarchyNode]:
    """Return neighboring nodes under the same parent, excluding the focus."""
    bounded = _bounded_limit(limit)
    node = get_node(conn, node_id)
    if node is None or node.parent_id is None:
        return []
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT * FROM nodes
               WHERE parent_id = ? AND document_id = ? AND node_id != ?
               ORDER BY position, node_id LIMIT ?""",
            (node.parent_id, node.document_id, node_id, bounded),
        ).fetchall()
        return [_node_from_row(row) for row in rows]
    finally:
        conn.row_factory = previous_factory


__all__ = [
    "HierarchyNode",
    "MAX_NAVIGATION_NODES",
    "NodeType",
    "get_children",
    "get_node",
    "get_parent",
    "get_siblings",
]
