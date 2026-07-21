from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from retrieval.indexer import build_index
from retrieval.mcp_server import validate_rag_db

_SAMPLE_HTML = """\
<html><head><title>Test Page</title></head><body>
<div class="header">Test Page</div>
<div id="oContent">
  <table class="SubHeader3"><tr><td id="_Hdr">Header</td></tr></table>
  <table class="SubList"><tr><td>Field</td><td>Description text here</td></tr></table>
</div></body></html>"""


@pytest.fixture()
def valid_db(tmp_path: Path) -> Path:
    html_path = tmp_path / "page.html"
    html_path.write_text(_SAMPLE_HTML, encoding="utf-8")
    db_path = tmp_path / "rag.sqlite"
    build_index(html_path, db_path)
    return db_path


def _copy(valid_db: Path, dest: Path) -> Path:
    shutil.copy(valid_db, dest)
    return dest


def test_valid_db_passes(valid_db: Path) -> None:
    validate_rag_db(valid_db)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="does not exist"):
        validate_rag_db(tmp_path / "nonexistent.sqlite")


def test_empty_file_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty.sqlite"
    empty.write_bytes(b"")
    with pytest.raises(SystemExit, match="empty"):
        validate_rag_db(empty)


def test_missing_table_raises(tmp_path: Path, valid_db: Path) -> None:
    db = _copy(valid_db, tmp_path / "notables.sqlite")
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE IF EXISTS chunks")
    conn.commit()
    conn.close()
    with pytest.raises(SystemExit, match="missing tables"):
        validate_rag_db(db)


def test_missing_index_version_raises(tmp_path: Path, valid_db: Path) -> None:
    db = _copy(valid_db, tmp_path / "nover.sqlite")
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM index_metadata WHERE key = 'index_version'")
    conn.commit()
    conn.close()
    with pytest.raises(SystemExit, match="index_version"):
        validate_rag_db(db)


def test_no_documents_raises(tmp_path: Path, valid_db: Path) -> None:
    db = _copy(valid_db, tmp_path / "nodocs.sqlite")
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM chunks_fts")
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM docs")
    conn.commit()
    conn.close()
    with pytest.raises(SystemExit, match="no indexed documents"):
        validate_rag_db(db)


def test_no_chunks_raises(tmp_path: Path, valid_db: Path) -> None:
    db = _copy(valid_db, tmp_path / "nochunks.sqlite")
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM chunks_fts")
    conn.execute("DELETE FROM chunks")
    conn.commit()
    conn.close()
    with pytest.raises(SystemExit, match="no indexed chunks"):
        validate_rag_db(db)


def test_missing_section_facts_table_raises(tmp_path: Path, valid_db: Path) -> None:
    db = _copy(valid_db, tmp_path / "nofacts.sqlite")
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE IF EXISTS section_facts")
    conn.commit()
    conn.close()
    with pytest.raises(SystemExit, match="missing tables"):
        validate_rag_db(db)


def test_orphaned_fts_raises(tmp_path: Path, valid_db: Path) -> None:
    db = _copy(valid_db, tmp_path / "orphan.sqlite")
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO chunks_fts(chunk_id, source_path, title, category, heading_path, text) "
        "VALUES ('orphan-chunk-id', 'fake.html', 'Fake', 'general', 'Fake > Section', 'fake text')"
    )
    conn.commit()
    conn.close()
    with pytest.raises(SystemExit, match="FTS entry with no matching chunk"):
        validate_rag_db(db)


def test_chunk_without_fts_entry_raises(tmp_path: Path, valid_db: Path) -> None:
    db = _copy(valid_db, tmp_path / "missing-fts.sqlite")
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM chunks_fts")
    conn.commit()
    conn.close()

    with pytest.raises(SystemExit, match="chunk with no matching FTS entry"):
        validate_rag_db(db)


def test_chunk_without_document_raises(tmp_path: Path, valid_db: Path) -> None:
    db = _copy(valid_db, tmp_path / "orphan-chunk.sqlite")
    conn = sqlite3.connect(str(db))
    chunk_id = "orphan-chunk"
    conn.execute(
        """INSERT INTO chunks
           (chunk_id, doc_id, chunk_index, category, heading_path, text, token_count, text_hash)
           VALUES (?, 'missing-doc', 2, 'general', 'Missing', 'orphan text', 3, 'hash')""",
        (chunk_id,),
    )
    conn.execute(
        "INSERT INTO chunks_fts(chunk_id, source_path, title, category, heading_path, text) "
        "VALUES (?, 'missing.html', 'Missing', 'general', 'Missing', 'orphan text')",
        (chunk_id,),
    )
    conn.execute("UPDATE index_metadata SET value = '2' WHERE key = 'chunk_count'")
    conn.commit()
    conn.close()

    with pytest.raises(SystemExit, match="chunk with no matching document"):
        validate_rag_db(db)


def test_duplicate_fts_entry_raises(tmp_path: Path, valid_db: Path) -> None:
    db = _copy(valid_db, tmp_path / "duplicate-fts.sqlite")
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT chunk_id, source_path, title, category, heading_path, text FROM chunks_fts LIMIT 1"
    ).fetchone()
    conn.execute(
        "INSERT INTO chunks_fts(chunk_id, source_path, title, category, heading_path, text) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        row,
    )
    conn.commit()
    conn.close()

    with pytest.raises(SystemExit, match="FTS entries"):
        validate_rag_db(db)


def test_manifest_count_mismatch_raises(tmp_path: Path, valid_db: Path) -> None:
    db = _copy(valid_db, tmp_path / "count-mismatch.sqlite")
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE index_metadata SET value = '99' WHERE key = 'chunk_count'")
    conn.commit()
    conn.close()

    with pytest.raises(SystemExit, match="chunk count does not match metadata"):
        validate_rag_db(db)
