from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from retrieval.hierarchy import (
    MAX_NAVIGATION_NODES,
    get_children,
    get_node,
    get_parent,
    get_siblings,
)
from retrieval.indexer import build_index


def _build_hierarchy(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    html_path = tmp_path / "appointments.html"
    html_path.write_text(
        """
        <html><head><title>CLARITY_APPOINTMENT</title></head><body>
        <div class="header">CLARITY_APPOINTMENT</div>
        <div id="oContent">
          <table class="SubHeader3"><tr><td id="_Overview">Overview</td></tr></table>
          <table class="List"><tr><td>Appointment documentation</td></tr></table>
          <table class="SubHeader3"><tr>
            <td id="____Column-Information____">Column Information</td>
          </tr></table>
          <table class="SubList List"><tbody>
            <tr><th></th><th>Name</th><th>INI</th><th>Item</th><th>Type</th></tr>
            <tr><td class="T1Head">1</td><td class="T1Head">APPT_STATUS_C</td>
              <td>EPT</td><td>100</td><td>NUMERIC</td></tr>
            <tr><td></td><td colspan="4">The appointment status category.</td></tr>
            <tr><td class="T1Head">2</td><td class="T1Head">VISITS</td>
              <td>EPT</td><td>101</td><td>INTEGER</td></tr>
            <tr><td></td><td colspan="4">Number of visits.</td></tr>
          </tbody></table>
        </div></body></html>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "rag.sqlite"
    build_index(html_path, db_path, workers=1)
    return db_path


def test_index_builds_document_section_and_leaf_nodes(tmp_path: Path) -> None:
    db_path = _build_hierarchy(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        root = conn.execute("SELECT * FROM nodes WHERE node_type = 'document'").fetchone()
        status_section = conn.execute(
            "SELECT * FROM nodes WHERE node_type = 'section' AND title = 'APPT_STATUS_C'"
        ).fetchone()
        status_leaf = conn.execute(
            "SELECT * FROM nodes WHERE node_type = 'leaf' AND title = 'APPT_STATUS_C'"
        ).fetchone()
        integrity_errors = conn.execute("PRAGMA foreign_key_check").fetchall()

    assert root is not None
    assert root["parent_id"] is None
    assert root["depth"] == 0
    assert status_section is not None
    assert status_section["depth"] == 2
    assert status_leaf is not None
    assert status_leaf["parent_id"] == status_section["node_id"]
    assert status_leaf["depth"] == 3
    assert status_leaf["chunk_id"]
    assert "appointment status category" in status_leaf["text"]
    assert integrity_errors == []


def test_bounded_parent_child_and_sibling_navigation(tmp_path: Path) -> None:
    db_path = _build_hierarchy(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        root_id = conn.execute("SELECT node_id FROM nodes WHERE node_type = 'document'").fetchone()[
            0
        ]
        status_id = conn.execute(
            "SELECT node_id FROM nodes WHERE node_type = 'section' AND title = 'APPT_STATUS_C'"
        ).fetchone()[0]

        root = get_node(conn, root_id)
        root_children = get_children(conn, root_id)
        status_parent = get_parent(conn, status_id)
        status_siblings = get_siblings(conn, status_id)

    assert root is not None and root.title == "CLARITY_APPOINTMENT"
    assert [node.title for node in root_children] == ["Overview", "Column-Information"]
    assert status_parent is not None and status_parent.title == "Column-Information"
    assert [node.title for node in status_siblings] == ["VISITS"]


def test_navigation_rejects_unbounded_results(tmp_path: Path) -> None:
    db_path = _build_hierarchy(tmp_path)
    with sqlite3.connect(db_path) as conn, pytest.raises(ValueError, match="limit"):
        get_children(conn, "anything", limit=MAX_NAVIGATION_NODES + 1)


def test_hierarchy_ids_are_stable_across_rebuilds(tmp_path: Path) -> None:
    first_path = _build_hierarchy(tmp_path / "first")
    second_path = _build_hierarchy(tmp_path / "second")

    def ids(db_path: Path) -> list[str]:
        with sqlite3.connect(db_path) as conn:
            return [row[0] for row in conn.execute("SELECT node_id FROM nodes ORDER BY node_id")]

    assert ids(first_path) == ids(second_path)
