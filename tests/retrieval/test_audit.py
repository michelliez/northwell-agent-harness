from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from retrieval.audit import AuditReport, _pct, print_report, run_audit
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


def test_print_report_accepts_character_percentiles(
    indexed_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    print_report(run_audit(indexed_db))

    output = capsys.readouterr().out
    assert "RAG Corpus Audit" in output
    assert "~tokens" in output


def test_percentile_uses_nearest_rank() -> None:
    assert _pct([10, 20], 50) == 10
    assert _pct([10, 20], 100) == 20
    with pytest.raises(ValueError, match="between 1 and 100"):
        _pct([10], 0)


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
    """A malformed legacy chunk above the hard limit must be reported."""
    long_text = "word " * (CHUNK_HARD_MAX_CHARS // 4)
    html_path = tmp_path / "long.html"
    html_path.write_text(
        f"<html><body>{long_text}</body></html>",
        encoding="utf-8",
    )
    db_path = tmp_path / "rag.sqlite"
    build_index(html_path, db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE chunks SET text = ? WHERE chunk_index = 0",
        ("x" * (CHUNK_HARD_MAX_CHARS + 1),),
    )
    conn.commit()
    conn.close()

    report = run_audit(db_path)
    assert report.over_limit_count >= 1
