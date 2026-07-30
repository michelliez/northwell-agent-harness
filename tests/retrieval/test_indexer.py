from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from bs4.element import Tag

from retrieval import indexer as indexer_module
from retrieval import search as search_module
from retrieval.indexer import (
    CHUNK_HARD_MAX_CHARS,
    CHUNK_TARGET_CHARS,
    SectionFact,
    build_index,
    extract_chunks,
    extract_table_content,
    table_to_chunks,
)


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

    context = search_module.retrieve_documentation_context("appointment status", db_path, top_k=5)

    assert doc_count == 1
    assert context["index_version"] == version
    assert context["chunks"]
    chunk = context["chunks"][0]
    assert chunk["source_path"] == "appointments.html"
    assert "Scheduled or completed" in chunk["text"]


def test_validation_rejects_incompatible_chunker_with_same_schema(tmp_path: Path) -> None:
    html_path = tmp_path / "page.html"
    html_path.write_text("<html><body>stable content</body></html>", encoding="utf-8")
    db_path = tmp_path / "rag.sqlite"
    build_index(html_path, db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE index_metadata SET value = ? WHERE key = 'chunker_version'",
            ("section-table-old-v4",),
        )

    with pytest.raises(SystemExit, match="incompatible chunker_version"):
        search_module.validate_index(db_path)


def test_retrieval_rejects_incompatible_chunker_with_same_schema(tmp_path: Path) -> None:
    html_path = tmp_path / "page.html"
    html_path.write_text("<html><body>stable content</body></html>", encoding="utf-8")
    db_path = tmp_path / "rag.sqlite"
    build_index(html_path, db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE index_metadata SET value = ? WHERE key = 'chunker_version'",
            ("section-table-old-v4",),
        )

    with pytest.raises(RuntimeError, match="incompatible chunker_version"):
        search_module.retrieve_documentation_context("stable", db_path)


def test_sqlite_column_chunks_use_canonical_genq_records(tmp_path: Path) -> None:
    """Production indexing must store GenQ column IDs, headings, and passage text."""
    from retrieval.column_parser import parse_epic_html

    html_path = tmp_path / "ACC_CONFIG_BLK.html"
    html_path.write_text(
        """
        <html><head><title>ACC_CONFIG_BLK</title></head><body>
        <div class="header">ACC_CONFIG_BLK</div>
        <div id="oContent">
          <table class="SubHeader3"><tr>
            <td id="____Column-Information____">Column Information</td>
          </tr></table>
          <table class="SubList List"><tbody>
            <tr><th></th><th>Name</th><th>INI</th><th>Item</th><th>Type</th></tr>
            <tr><td class="T1Head">1</td><td class="T1Head">CONFIG_ID</td>
              <td>SNR</td><td>.1</td><td>NUMERIC (18,0)</td></tr>
            <tr><td></td><td colspan="4">The accessibility configuration identifier.</td></tr>
            <tr><td class="T1Head">2</td><td class="T1Head">LINE</td>
              <td></td><td></td><td>INTEGER</td></tr>
            <tr><td></td><td colspan="4">The block restriction line number.</td></tr>
          </tbody></table>
        </div></body></html>
        """,
        encoding="utf-8",
    )
    expected = [
        record
        for record in parse_epic_html(html_path, corpus_root=tmp_path).records
        if record.chunk_type == "column_definition"
    ]
    db_path = tmp_path / "rag.sqlite"

    build_index(html_path, db_path)

    with sqlite3.connect(db_path) as conn:
        stored = conn.execute(
            """
            SELECT chunk_id, heading_path, text
            FROM chunks
            WHERE category = 'column_info'
            ORDER BY chunk_index
            """
        ).fetchall()
    assert stored == [
        (
            record.chunk_id,
            f"{record.table_name} > Column-Information > {record.column_name}",
            record.text,
        )
        for record in expected
    ]


def test_search_with_no_fts_tokens_returns_no_results(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "simple.html"
    html_path.write_text("<html><body>Simple documentation</body></html>", encoding="utf-8")
    db_path = tmp_path / "rag.sqlite"
    build_index(html_path, db_path)

    result = search_module.retrieve_documentation_context("---", db_path, top_k=5)

    assert result["chunks"] == []


def test_public_retrieval_rejects_unbounded_top_k(tmp_path: Path) -> None:
    db_path = tmp_path / "nonexistent.sqlite"
    with pytest.raises(ValueError, match="top_k"):
        search_module.retrieve_documentation_context("test", db_path, top_k=26)


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
    categories = {row[0] for row in conn.execute("SELECT DISTINCT category FROM chunks").fetchall()}
    fact_count = conn.execute("SELECT COUNT(*) FROM section_facts").fetchone()[0]
    conn.close()

    assert doc_count == 1
    assert chunk_count == 1  # only the SubList chunk; the NA section is dropped
    assert "section_empty" not in categories
    assert fact_count == 1  # NA section preserved as a structured fact, not discarded


def test_document_with_only_empty_structured_sections_has_no_fallback_chunk() -> None:
    _title, chunks, _facts = extract_chunks(
        """
        <html><head><title>Empty</title></head><body>
        <div class="header">Empty</div>
        <div id="oContent">
          <table class="SubHeader3"><tr><td id="_Empty">Empty</td></tr></table>
          <span class="NA">No data</span>
        </div></body></html>
        """,
        fallback_title="empty",
    )

    assert chunks == []


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
    assert max_chars <= CHUNK_TARGET_CHARS


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>" + ("fallback text " * 1000) + "</body></html>",
        (
            "<html><body><div id='oContent'><table class='KeyValue'>"
            f"<tr><td>{'metadata value ' * 1000}</td></tr>"
            "</table></div></body></html>"
        ),
        (
            "<html><body><div id='oContent'>"
            "<table class='SubHeader3'><tr><td id='_Long'>Long</td></tr></table>"
            f"<table class='SubList'><tr><td>{'single row value ' * 1000}</td></tr></table>"
            "</div></body></html>"
        ),
    ],
)
def test_all_chunk_sources_enforce_size_limit(html: str) -> None:
    _title, chunks, _facts = extract_chunks(html, fallback_title="long")

    assert len(chunks) > 1
    assert all(0 < len(chunk.text) <= CHUNK_TARGET_CHARS for chunk in chunks)
    assert all(len(chunk.text) <= CHUNK_HARD_MAX_CHARS for chunk in chunks)


def test_nested_table_rows_and_cells_are_processed_once() -> None:
    soup = BeautifulSoup(
        """
        <table class="SubList List">
          <tbody>
          <tr><th>Name</th><th>Value</th></tr>
          <tr>
            <td class="T1Head">Outer row one</td>
            <td>
              <table class="SubList">
                <tr><td class="T1Head">Nested heading</td><td>Nested value</td></tr>
              </table>
            </td>
          </tr>
          <tr><td>Outer row two</td><td>Second value</td></tr>
          </tbody>
        </table>
        """,
        "html.parser",
    )
    table = soup.find("table")
    assert table is not None

    rendered = extract_table_content(table)
    chunks = table_to_chunks("column_info", "Example > Columns", table)
    chunk_text = " | ".join(chunk.text for chunk in chunks)

    assert rendered.count("Nested heading") == 1
    assert chunk_text.count("Nested heading") == 1
    assert chunk_text.count("Outer row one") == 1
    assert chunk_text.count("Outer row two") == 1
    assert chunk_text.startswith("Name Value | Outer row one:")
    assert not chunk_text.startswith("Outer row one:")


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


def test_index_version_changes_with_chunker_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = tmp_path / "page.html"
    html_path.write_text("<html><body>stable content</body></html>", encoding="utf-8")

    version_v1, _, _ = build_index(html_path, tmp_path / "v1.sqlite")
    monkeypatch.setattr(indexer_module, "CHUNKER_VERSION", "section-table-test-v3")
    version_v2, _, _ = build_index(html_path, tmp_path / "v2.sqlite")

    assert version_v1 != version_v2


def test_index_version_changes_when_source_path_changes(tmp_path: Path) -> None:
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"
    content = "<html><body>identical content</body></html>"
    first.write_text(content, encoding="utf-8")
    second.write_text(content, encoding="utf-8")

    version_first, _, _ = build_index(first, tmp_path / "first.sqlite")
    version_second, _, _ = build_index(second, tmp_path / "second.sqlite")

    assert version_first != version_second


def test_extract_chunks_skips_none_sibling(tmp_path: Path) -> None:
    """A SubHeader3 whose next sibling stringifies to 'None' must be dropped."""
    _title, chunks, _facts = extract_chunks(
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


# ---------------------------------------------------------------------------
# Coverage: every owned content-table row must appear in at least one chunk
# ---------------------------------------------------------------------------


def test_every_content_row_covered_by_chunk() -> None:
    """Every owned cell must appear exactly once in the chunk output."""
    markers = [
        marker
        for i in range(12)
        for marker in (f"COVERAGE_FIELD_{i:03d}", f"COVERAGE_VALUE_{i:03d}")
    ]
    rows_html = "".join(
        f"<tr><td>COVERAGE_FIELD_{i:03d}</td><td>COVERAGE_VALUE_{i:03d} description text</td></tr>"
        for i in range(12)
    )
    _title, chunks, _facts = extract_chunks(
        f"""<html><head><title>Coverage</title></head><body>
        <div class="header">Coverage</div>
        <div id="oContent">
          <table class="SubHeader3"><tr><td id="_F">Fields</td></tr></table>
          <table class="SubList">{rows_html}</table>
        </div></body></html>""",
        fallback_title="coverage",
    )
    all_text = " ".join(c.text for c in chunks)
    wrong_counts = {
        marker: all_text.count(marker) for marker in markers if all_text.count(marker) != 1
    }
    assert not wrong_counts, f"cells with incorrect chunk coverage: {wrong_counts}"


def test_every_content_row_covered_after_split() -> None:
    """Every owned cell must appear once after a table is split into chunks."""
    count = 120  # enough rows to force table_to_chunks to split
    markers = [
        marker for i in range(count) for marker in (f"SPLIT_FIELD_{i:04d}", f"SPLIT_VALUE_{i:04d}")
    ]
    rows_html = "".join(
        f"<tr><td>SPLIT_FIELD_{i:04d}</td>"
        f"<td>SPLIT_VALUE_{i:04d} extra text to push past the split threshold</td></tr>"
        for i in range(count)
    )
    _title, chunks, _facts = extract_chunks(
        f"""<html><head><title>Split</title></head><body>
        <div class="header">Split</div>
        <div id="oContent">
          <table class="SubHeader3"><tr><td id="_F">Fields</td></tr></table>
          <table class="SubList">{rows_html}</table>
        </div></body></html>""",
        fallback_title="split",
    )
    assert len(chunks) > 1, "test requires splitting to occur"
    all_text = " ".join(c.text for c in chunks)
    wrong_counts = {
        marker: all_text.count(marker) for marker in markers if all_text.count(marker) != 1
    }
    assert not wrong_counts, f"cells with incorrect split coverage: {wrong_counts}"


def test_malformed_unclosed_table_rows_do_not_expand_cumulatively() -> None:
    """Legacy unclosed cells must not repeatedly absorb all subsequent rows."""
    count = 80
    markers = [
        marker
        for i in range(count)
        for marker in (f"MALFORMED_FIELD_{i:04d}", f"MALFORMED_VALUE_{i:04d}")
    ]
    rows_html = "".join(
        f"<tr><td>MALFORMED_FIELD_{i:04d}<td>MALFORMED_VALUE_{i:04d}" for i in range(count)
    )
    soup = BeautifulSoup(f"<table class='List'>{rows_html}</table>", "html.parser")
    table = soup.find("table")
    assert isinstance(table, Tag)

    chunks = table_to_chunks("table_data", "Example > Foreign-Key-Information", table)
    all_text = " ".join(chunk.text for chunk in chunks)
    wrong_counts = {
        marker: all_text.count(marker) for marker in markers if all_text.count(marker) != 1
    }

    assert not wrong_counts, f"malformed cells with incorrect coverage: {wrong_counts}"
    assert len(chunks) <= 3


# ---------------------------------------------------------------------------
# Section facts: present-but-unavailable sections must be recorded
# ---------------------------------------------------------------------------


def test_section_fact_recorded_for_na_span() -> None:
    """An NA span must produce a SectionFact and no chunk."""
    _title, chunks, facts = extract_chunks(
        """<html><head><title>T</title></head><body>
        <div class="header">T</div>
        <div id="oContent">
          <table class="SubHeader3"><tr><td id="_Missing">Missing</td></tr></table>
          <span class="NA">No data</span>
        </div></body></html>""",
        fallback_title="t",
    )
    assert chunks == []
    assert len(facts) == 1
    assert facts[0] == SectionFact(heading_path="T > Missing", fact="present_but_unavailable")


def test_section_fact_recorded_when_section_has_no_sibling() -> None:
    """A declared section with no following value must produce a SectionFact."""
    _title, chunks, facts = extract_chunks(
        """<html><head><title>T</title></head><body>
        <div class="header">T</div>
        <div id="oContent">
          <table class="SubHeader3"><tr><td id="_Missing">Missing</td></tr></table>
        </div></body></html>""",
        fallback_title="t",
    )

    assert chunks == []
    assert facts == [SectionFact(heading_path="T > Missing", fact="present_but_unavailable")]


def test_section_facts_stored_in_db(tmp_path: Path) -> None:
    """section_facts rows must be written to the database by build_index."""
    html_path = tmp_path / "page.html"
    html_path.write_text(
        """<html><head><title>Mixed</title></head><body>
        <div class="header">Mixed</div>
        <div id="oContent">
          <table class="SubHeader3"><tr><td id="_Empty">Empty</td></tr></table>
          <span class="NA">No data</span>
          <table class="SubHeader3"><tr><td id="_Data">Data</td></tr></table>
          <table class="SubList"><tr><td>Field</td><td>Value</td></tr></table>
        </div></body></html>""",
        encoding="utf-8",
    )
    db_path = tmp_path / "rag.sqlite"
    build_index(html_path, db_path)

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT heading_path, fact FROM section_facts").fetchall()
    conn.close()

    assert len(rows) == 1
    heading_path, fact = rows[0]
    assert "Empty" in heading_path
    assert fact == "present_but_unavailable"
