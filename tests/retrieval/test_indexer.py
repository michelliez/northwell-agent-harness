from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from retrieval import mcp_server
from retrieval.indexer import CHUNK_TARGET_CHARS, build_index, extract_chunks


def test_build_and_search_rag_index(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    html_path = tmp_path / "appointments.html"
    html_path.write_text(
        """
        <html><head><title>Appointments</title></head><body>
        <div class="header">Appointments</div>
        <div id="oContent">
          <table class="SubHeader3"><tr><td id="_Status">Status</td></tr></table>
          <table class="SubList"><tr><td>Status</td><td>Scheduled or completed</td></tr></table>
        </div></body></html>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "rag.sqlite"
    version, doc_count, _chunk_count = build_index(html_path, db_path)
    monkeypatch.setenv("RAG_DB_PATH", str(db_path))

    result = mcp_server.search_docs("appointment status", top_k=5)

    assert doc_count == 1
    assert result["index_version"] == version
    assert result["results"]
    chunk = mcp_server.get_doc_chunk(result["results"][0]["chunk_id"])
    assert chunk["source_path"] == "appointments.html"
    assert "Scheduled or completed" in chunk["text"]


def test_search_with_no_fts_tokens_returns_no_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = tmp_path / "simple.html"
    html_path.write_text("<html><body>Simple documentation</body></html>", encoding="utf-8")
    db_path = tmp_path / "rag.sqlite"
    build_index(html_path, db_path)
    monkeypatch.setenv("RAG_DB_PATH", str(db_path))

    result = mcp_server.search_docs("---", top_k=5)

    assert result["results"] == []


def test_section_empty_not_indexed(tmp_path: Path) -> None:
    """NA spans and None siblings must be skipped; only real content is indexed."""
    html_path = tmp_path / "page.html"
    html_path.write_text(
        """
        <html><head><title>Mixed</title></head><body>
        <div class="header">Mixed</div>
        <div id="oContent">
          <table class="SubHeader3"><tr><td id="_Empty">Empty</td></tr></table>
          <span class="NA">No data</span>
          <table class="SubHeader3"><tr><td id="_Status">Status</td></tr></table>
          <table class="SubList"><tr><td>Active</td><td>Currently active</td></tr></table>
        </div></body></html>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "rag.sqlite"
    _version, doc_count, chunk_count = build_index(html_path, db_path)

    conn = sqlite3.connect(str(db_path))
    categories = {
        row[0] for row in conn.execute("SELECT DISTINCT category FROM chunks").fetchall()
    }
    conn.close()

    assert doc_count == 1
    assert chunk_count == 1  # only the SubList chunk; the NA section is dropped
    assert "section_empty" not in categories


def test_large_table_splits_into_multiple_chunks(tmp_path: Path) -> None:
    """A table whose rows sum past CHUNK_TARGET_CHARS must produce more than one chunk."""
    # Each row ~65 chars; 80 rows ≈ 5,200 chars > CHUNK_TARGET_CHARS
    rows_html = "".join(
        f"<tr><td>FieldName{i:03d}</td>"
        f"<td>This is the description value for field number {i:03d}</td></tr>"
        for i in range(80)
    )
    html_path = tmp_path / "big.html"
    html_path.write_text(
        f"""
        <html><head><title>BigTable</title></head><body>
        <div class="header">BigTable</div>
        <div id="oContent">
          <table class="SubHeader3"><tr><td id="_Cols">Columns</td></tr></table>
          <table class="SubList">{rows_html}</table>
        </div></body></html>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "rag.sqlite"
    _version, _doc_count, chunk_count = build_index(html_path, db_path)

    assert chunk_count > 1, "large table should be split into multiple chunks"

    conn = sqlite3.connect(str(db_path))
    max_chars = conn.execute("SELECT MAX(length(text)) FROM chunks").fetchone()[0]
    conn.close()
    assert max_chars <= CHUNK_TARGET_CHARS * 2  # no runaway chunk


def test_token_count_stored(tmp_path: Path) -> None:
    """Every indexed chunk must have a positive token_count value."""
    html_path = tmp_path / "page.html"
    html_path.write_text(
        """
        <html><head><title>T</title></head><body>
        <div class="header">T</div>
        <div id="oContent">
          <table class="SubHeader3"><tr><td id="_F">F</td></tr></table>
          <table class="SubList"><tr><td>Col</td><td>Value here</td></tr></table>
        </div></body></html>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "rag.sqlite"
    build_index(html_path, db_path)

    conn = sqlite3.connect(str(db_path))
    bad = conn.execute("SELECT COUNT(*) FROM chunks WHERE token_count < 1").fetchone()[0]
    conn.close()

    assert bad == 0


def test_doc_id_stable_across_content_change(tmp_path: Path) -> None:
    """doc_id must be path-based and must not change when file content changes."""
    html_path = tmp_path / "page.html"
    db_v1 = tmp_path / "v1.sqlite"
    db_v2 = tmp_path / "v2.sqlite"

    html_path.write_text(
        "<html><head><title>V1</title></head><body>"
        "<div id='oContent'>content version one</div></body></html>",
        encoding="utf-8",
    )
    build_index(html_path, db_v1)

    html_path.write_text(
        "<html><head><title>V2</title></head><body>"
        "<div id='oContent'>content version two entirely different</div></body></html>",
        encoding="utf-8",
    )
    build_index(html_path, db_v2)

    conn1 = sqlite3.connect(str(db_v1))
    conn2 = sqlite3.connect(str(db_v2))
    try:
        doc_id_v1 = conn1.execute("SELECT doc_id FROM docs").fetchone()[0]
        doc_id_v2 = conn2.execute("SELECT doc_id FROM docs").fetchone()[0]
    finally:
        conn1.close()
        conn2.close()

    assert doc_id_v1 == doc_id_v2


def test_chunk_id_stable_across_reindex(tmp_path: Path) -> None:
    """An unchanged chunk must keep its chunk_id when a sibling section is updated."""
    stable = (
        "<table class='SubHeader3'><tr><td id='_Status'>Status</td></tr></table>"
        "<table class='SubList'><tr><td>Active</td><td>Currently active</td></tr></table>"
    )

    def make_html(type_value: str) -> str:
        return (
            "<html><head><title>Doc</title></head><body>"
            "<div class='header'>Doc</div>"
            "<div id='oContent'>"
            f"{stable}"
            "<table class='SubHeader3'><tr><td id='_Type'>Type</td></tr></table>"
            f"<table class='SubList'><tr><td>Kind</td><td>{type_value}</td></tr></table>"
            "</div></body></html>"
        )

    html_path = tmp_path / "page.html"
    db_v1 = tmp_path / "v1.sqlite"
    db_v2 = tmp_path / "v2.sqlite"

    html_path.write_text(make_html("Alpha"), encoding="utf-8")
    build_index(html_path, db_v1)

    html_path.write_text(make_html("Beta"), encoding="utf-8")
    build_index(html_path, db_v2)

    conn1 = sqlite3.connect(str(db_v1))
    conn2 = sqlite3.connect(str(db_v2))
    try:
        id_v1 = conn1.execute(
            "SELECT chunk_id FROM chunks WHERE heading_path LIKE '%Status%'"
        ).fetchone()[0]
        id_v2 = conn2.execute(
            "SELECT chunk_id FROM chunks WHERE heading_path LIKE '%Status%'"
        ).fetchone()[0]
    finally:
        conn1.close()
        conn2.close()

    assert id_v1 == id_v2


def test_extract_chunks_skips_none_sibling(tmp_path: Path) -> None:
    """A SubHeader3 whose next sibling stringifies to 'None' must be dropped."""
    _title, chunks = extract_chunks(
        """
        <html><head><title>X</title></head><body>
        <div class="header">X</div>
        <div id="oContent">
          <table class="SubHeader3"><tr><td id="_S">S</td></tr></table>
          <table class="SubList"><tr><td>Real</td><td>content</td></tr></table>
        </div></body></html>
        """,
        fallback_title="x",
    )
    categories = {c.category for c in chunks}
    assert "section_empty" not in categories
    assert any("Real" in c.text for c in chunks)
