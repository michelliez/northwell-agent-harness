from __future__ import annotations

from pathlib import Path

import pytest

from harness_spike.mcp_servers import rag_retrieval
from harness_spike.rag_index import build_index


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
    version, count = build_index(html_path, db_path)
    monkeypatch.setenv("RAG_DB_PATH", str(db_path))

    result = rag_retrieval.search_docs("appointment status", top_k=5)

    assert count == 1
    assert result["index_version"] == version
    assert result["results"]
    chunk = rag_retrieval.get_doc_chunk(result["results"][0]["chunk_id"])
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

    result = rag_retrieval.search_docs("---", top_k=5)

    assert result["results"] == []
