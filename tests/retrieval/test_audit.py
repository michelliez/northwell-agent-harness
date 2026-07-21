from __future__ import annotations

from pathlib import Path

import pytest

from retrieval.audit import AuditReport, run_audit
from retrieval.indexer import CHUNK_HARD_MAX_CHARS, build_index

_SAMPLE_HTML = """\
<html><head><title>Audit Test</title></head><body>
<div class="header">Audit Test</div>
<div id="oContent">
  <table class="SubHeader3"><tr><td id="_Hdr">Header</td></tr></table>
  <table class="SubList">
    <tr><td>Field A</td><td>Description alpha</td></tr>
    <tr><td>Field B</td><td>Description beta</td></tr>
  </table>
</div></body></html>"""


@pytest.fixture()
def indexed_db(tmp_path: Path) -> Path:
    html_path = tmp_path / "page.html"
    html_path.write_text(_SAMPLE_HTML, encoding="utf-8")
    db_path = tmp_path / "rag.sqlite"
    build_index(html_path, db_path)
    return db_path


def test_run_audit_returns_report(indexed_db: Path) -> None:
    report = run_audit(indexed_db)

    assert isinstance(report, AuditReport)
    assert report.doc_count == 1
    assert report.chunk_count >= 1
    assert report.char_p50 > 0
    assert report.char_max > 0
    assert report.char_p50 <= report.char_p90 <= report.char_p95 <= report.char_p99 <= report.char_max
    assert report.over_limit_count == 0  # sample data is small


def test_audit_category_breakdown(indexed_db: Path) -> None:
    report = run_audit(indexed_db)

    assert "column_info" in report.by_category
    assert report.by_category.get("section_empty", 0) == 0


def test_audit_empty_count_for_clean_index(indexed_db: Path) -> None:
    report = run_audit(indexed_db)

    assert report.empty_count == 0
    assert report.duplicate_text_hash_count == 0


def test_audit_raises_on_missing_db(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="not found"):
        run_audit(tmp_path / "nonexistent.sqlite")


def test_audit_detects_over_limit(tmp_path: Path) -> None:
    """A chunk exceeding CHUNK_HARD_MAX_CHARS must be counted in over_limit_count."""
    # Build an index with a single very-long text (fallback document chunk)
    long_text = "word " * (CHUNK_HARD_MAX_CHARS // 4)  # guaranteed to exceed limit
    html_path = tmp_path / "long.html"
    html_path.write_text(
        f"<html><body>{long_text}</body></html>",
        encoding="utf-8",
    )
    db_path = tmp_path / "rag.sqlite"
    build_index(html_path, db_path)

    report = run_audit(db_path)
    assert report.over_limit_count >= 1
