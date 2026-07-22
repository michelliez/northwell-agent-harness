from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import pytest

from evals.retrieval_evaluator import (
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    run_retrieval_evaluation,
)
from retrieval.indexer import build_index


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_ranking_metrics_use_binary_relevance_for_recall_and_graded_ndcg() -> None:
    grades = [3, 0, 2]

    assert precision_at_k(grades, 2) == 0.5
    assert recall_at_k(grades, relevant_total=3, k=2) == pytest.approx(1 / 3)
    assert recall_at_k(grades, relevant_total=0, k=2) is None
    assert reciprocal_rank(grades) == 1.0

    expected_dcg = 7 / math.log2(2) + 3 / math.log2(4)
    ideal_dcg = 7 / math.log2(2) + 3 / math.log2(3)
    assert ndcg_at_k(grades, [3, 2], 3) == pytest.approx(expected_dcg / ideal_dcg)


def test_retrieval_evaluator_scores_fts_against_qrels(tmp_path: Path) -> None:
    html_path = tmp_path / "appointments.html"
    html_path.write_text(
        """
        <html><head><title>Appointments</title></head><body>
        <div class="header">Appointments</div><div id="oContent">
          <table class="SubHeader3"><tr><td id="_Status">Status</td></tr></table>
          <table class="SubList"><tr><td>Status</td><td>Scheduled or completed</td></tr></table>
        </div></body></html>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "rag.sqlite"
    index_version, _, _ = build_index(html_path, db_path)
    conn = sqlite3.connect(db_path)
    chunk_id, doc_id = conn.execute("SELECT chunk_id, doc_id FROM chunks").fetchone()
    conn.close()

    queries_path = tmp_path / "queries.jsonl"
    qrels_path = tmp_path / "qrels.jsonl"
    _write_jsonl(
        queries_path,
        [
            {
                "query_id": "Q1",
                "query": "appointment status",
                "category": "column_definition",
                "answerable": True,
                "ground_truth_complete": True,
                "index_version": index_version,
            }
        ],
    )
    _write_jsonl(
        qrels_path,
        [
            {
                "query_id": "Q1",
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "relevance": 3,
            }
        ],
    )

    report = run_retrieval_evaluation(
        db_path=db_path,
        queries_path=queries_path,
        qrels_path=qrels_path,
        k_values=(1, 5),
    )

    assert report["judgment_complete"] is True
    assert report["unjudged_total"] == 0
    assert report["metrics"]["chunk"]["recall@1"] == 1.0
    assert report["metrics"]["document"]["ndcg@1"] == 1.0
    assert report["results"][0]["results"][0]["relevance"] == 3


def test_retrieval_evaluator_flags_unjudged_results(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name, body in (
        ("first.html", "appointment status scheduled"),
        ("second.html", "appointment status completed"),
    ):
        (corpus / name).write_text(
            f"<html><head><title>{name}</title></head><body>{body}</body></html>",
            encoding="utf-8",
        )
    db_path = tmp_path / "rag.sqlite"
    index_version, _, _ = build_index(corpus, db_path)
    conn = sqlite3.connect(db_path)
    chunk_id, doc_id = conn.execute(
        """
        SELECT c.chunk_id, c.doc_id
        FROM chunks AS c JOIN docs AS d ON d.doc_id = c.doc_id
        WHERE d.source_path = 'first.html'
        """
    ).fetchone()
    conn.close()

    queries_path = tmp_path / "queries.jsonl"
    qrels_path = tmp_path / "qrels.jsonl"
    _write_jsonl(
        queries_path,
        [
            {
                "query_id": "Q1",
                "query": "appointment status",
                "category": "test",
                "answerable": True,
                "ground_truth_complete": True,
                "index_version": index_version,
            }
        ],
    )
    _write_jsonl(
        qrels_path,
        [
            {
                "query_id": "Q1",
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "relevance": 3,
            }
        ],
    )

    report = run_retrieval_evaluation(
        db_path=db_path,
        queries_path=queries_path,
        qrels_path=qrels_path,
        k_values=(2,),
    )

    assert report["judgment_complete"] is False
    assert report["unjudged_total"] == 1
    assert report["results"][0]["metrics"] is None
