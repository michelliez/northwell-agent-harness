from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from retrieval.mcp_server import search_ranked_chunks


@dataclass(frozen=True)
class RetrievalQuery:
    query_id: str
    query: str
    category: str
    answerable: bool
    ground_truth_complete: bool
    index_version: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RetrievalQuery:
        return cls(
            query_id=str(data["query_id"]),
            query=str(data["query"]),
            category=str(data["category"]),
            answerable=bool(data["answerable"]),
            ground_truth_complete=bool(data["ground_truth_complete"]),
            index_version=str(data["index_version"]),
        )


@dataclass(frozen=True)
class RetrievalQrel:
    query_id: str
    chunk_id: str
    doc_id: str
    relevance: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RetrievalQrel:
        relevance = int(data["relevance"])
        if relevance not in {0, 1, 2, 3}:
            raise ValueError("relevance must be one of 0, 1, 2, or 3")
        return cls(
            query_id=str(data["query_id"]),
            chunk_id=str(data["chunk_id"]),
            doc_id=str(data["doc_id"]),
            relevance=relevance,
        )


@dataclass(frozen=True)
class RankedChunk:
    chunk_id: str
    doc_id: str
    source_path: str
    heading_path: str
    score: float | None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected a JSON object")
        rows.append(value)
    if not rows:
        raise ValueError(f"{path}: no rows found")
    return rows


def load_retrieval_dataset(
    queries_path: Path, qrels_path: Path
) -> tuple[list[RetrievalQuery], list[RetrievalQrel]]:
    try:
        queries = [RetrievalQuery.from_dict(row) for row in _load_jsonl(queries_path)]
        qrels = [RetrievalQrel.from_dict(row) for row in _load_jsonl(qrels_path)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid retrieval evaluation dataset: {exc}") from exc

    query_ids = [query.query_id for query in queries]
    if len(set(query_ids)) != len(query_ids):
        raise ValueError("retrieval query IDs must be unique")
    known_query_ids = set(query_ids)
    seen_qrels: set[tuple[str, str]] = set()
    positive_counts = {query_id: 0 for query_id in query_ids}
    for qrel in qrels:
        if qrel.query_id not in known_query_ids:
            raise ValueError(f"qrel references unknown query {qrel.query_id}")
        key = (qrel.query_id, qrel.chunk_id)
        if key in seen_qrels:
            raise ValueError(f"duplicate qrel for query/chunk {key}")
        seen_qrels.add(key)
        if qrel.relevance > 0:
            positive_counts[qrel.query_id] += 1

    for query in queries:
        if query.answerable != (positive_counts[query.query_id] > 0):
            raise ValueError(
                f"{query.query_id} answerability disagrees with its positive judgments"
            )
    return queries, qrels


def precision_at_k(grades: Sequence[int], k: int) -> float:
    _validate_k(k)
    return sum(grade > 0 for grade in grades[:k]) / k


def recall_at_k(grades: Sequence[int], relevant_total: int, k: int) -> float | None:
    _validate_k(k)
    if relevant_total == 0:
        return None
    return sum(grade > 0 for grade in grades[:k]) / relevant_total


def ndcg_at_k(grades: Sequence[int], ideal_grades: Sequence[int], k: int) -> float | None:
    _validate_k(k)

    def dcg(values: Sequence[int]) -> float:
        return sum(
            ((2**grade) - 1) / math.log2(rank + 1)
            for rank, grade in enumerate(values[:k], start=1)
        )

    ideal = dcg(sorted(ideal_grades, reverse=True))
    if ideal == 0:
        return None
    return dcg(grades) / ideal


def reciprocal_rank(grades: Sequence[int]) -> float:
    for rank, grade in enumerate(grades, start=1):
        if grade > 0:
            return 1 / rank
    return 0.0


def _validate_k(k: int) -> None:
    if k < 1:
        raise ValueError("K must be at least 1")


def _ranked_metrics(
    ranked_ids: Sequence[str],
    relevance_by_id: Mapping[str, int],
    k_values: Sequence[int],
) -> dict[str, float | bool | None]:
    grades = [relevance_by_id.get(item_id, 0) for item_id in ranked_ids]
    ideal_grades = list(relevance_by_id.values())
    relevant_total = sum(grade > 0 for grade in ideal_grades)
    metrics: dict[str, float | bool | None] = {
        "mrr": reciprocal_rank(grades),
        "relevant_total": float(relevant_total),
    }
    for k in k_values:
        metrics[f"precision@{k}"] = precision_at_k(grades, k)
        metrics[f"recall@{k}"] = recall_at_k(grades, relevant_total, k)
        metrics[f"ndcg@{k}"] = ndcg_at_k(grades, ideal_grades, k)
        metrics[f"hit@{k}"] = any(grade > 0 for grade in grades[:k])
    return metrics


def _macro_average(
    results: Sequence[Mapping[str, Any]], level: str, k_values: Sequence[int]
) -> dict[str, float | None]:
    names = ["mrr"]
    for k in k_values:
        names.extend((f"precision@{k}", f"recall@{k}", f"ndcg@{k}", f"hit@{k}"))
    summary: dict[str, float | None] = {}
    for name in names:
        values: list[float] = []
        for result in results:
            if not result["answerable"] or result["metrics"] is None:
                continue
            value = result["metrics"][level][name]
            if value is not None:
                values.append(float(value))
        summary[name] = sum(values) / len(values) if values else None
    return summary


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _validate_against_index(
    conn: sqlite3.Connection,
    queries: Sequence[RetrievalQuery],
    qrels: Sequence[RetrievalQrel],
) -> str:
    row = conn.execute(
        "SELECT value FROM index_metadata WHERE key = 'index_version'"
    ).fetchone()
    if row is None:
        raise ValueError("RAG index is missing index_version")
    index_version = str(row[0])
    for query in queries:
        if query.index_version != index_version:
            raise ValueError(
                f"{query.query_id} targets index {query.index_version}, active index is {index_version}"
            )
    for qrel in qrels:
        actual = conn.execute(
            "SELECT doc_id FROM chunks WHERE chunk_id = ?", (qrel.chunk_id,)
        ).fetchone()
        if actual is None or str(actual[0]) != qrel.doc_id:
            raise ValueError(
                f"{qrel.query_id}/{qrel.chunk_id} does not match the active index"
            )
    return index_version


def _fts_retrieve(
    conn: sqlite3.Connection, query: str, top_k: int
) -> tuple[list[RankedChunk], float]:
    started = perf_counter()
    raw_results = search_ranked_chunks(conn.cursor(), query=query, top_k=top_k)
    ranked: list[RankedChunk] = []
    for result in raw_results:
        chunk_id = str(result["chunk_id"])
        row = conn.execute(
            """
            SELECT c.doc_id, c.heading_path, d.source_path
            FROM chunks AS c
            JOIN docs AS d ON d.doc_id = c.doc_id
            WHERE c.chunk_id = ?
            """,
            (chunk_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"retriever returned missing chunk {chunk_id}")
        score = result.get("score")
        numeric_score = float(score) if isinstance(score, (int, float)) else None
        ranked.append(
            RankedChunk(
                chunk_id=chunk_id,
                doc_id=str(row["doc_id"]),
                source_path=str(row["source_path"]),
                heading_path=str(row["heading_path"]),
                score=numeric_score,
            )
        )
    latency_ms = (perf_counter() - started) * 1000
    return ranked, latency_ms


def run_retrieval_evaluation(
    *,
    db_path: Path,
    queries_path: Path,
    qrels_path: Path,
    retriever: str = "fts",
    k_values: Sequence[int] = (5, 10),
) -> dict[str, Any]:
    if retriever != "fts":
        raise ValueError(f"unsupported retriever: {retriever}")
    normalized_k = tuple(sorted(set(k_values)))
    if not normalized_k:
        raise ValueError("at least one K value is required")
    for k in normalized_k:
        _validate_k(k)

    queries, qrels = load_retrieval_dataset(queries_path, qrels_path)
    qrels_by_query: dict[str, list[RetrievalQrel]] = defaultdict(list)
    for qrel in qrels:
        qrels_by_query[qrel.query_id].append(qrel)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        index_version = _validate_against_index(conn, queries, qrels)
        results: list[dict[str, Any]] = []
        max_k = max(normalized_k)
        for query in queries:
            query_qrels = qrels_by_query[query.query_id]
            chunk_relevance = {qrel.chunk_id: qrel.relevance for qrel in query_qrels}
            doc_relevance: dict[str, int] = {}
            for qrel in query_qrels:
                doc_relevance[qrel.doc_id] = max(
                    doc_relevance.get(qrel.doc_id, 0), qrel.relevance
                )

            ranked, latency_ms = _fts_retrieve(conn, query.query, max_k)
            unjudged = [item.chunk_id for item in ranked if item.chunk_id not in chunk_relevance]
            metrics: dict[str, Any] | None = None
            if not unjudged:
                ranked_doc_ids = list(dict.fromkeys(item.doc_id for item in ranked))
                metrics = {
                    "chunk": _ranked_metrics(
                        [item.chunk_id for item in ranked], chunk_relevance, normalized_k
                    ),
                    "document": _ranked_metrics(
                        ranked_doc_ids, doc_relevance, normalized_k
                    ),
                }

            retrieved_chunk_ids = {item.chunk_id for item in ranked}
            retrieved_doc_ids = {item.doc_id for item in ranked}
            results.append(
                {
                    "query_id": query.query_id,
                    "query": query.query,
                    "category": query.category,
                    "answerable": query.answerable,
                    "ground_truth_complete": query.ground_truth_complete,
                    "latency_ms": latency_ms,
                    "judgment_complete": not unjudged,
                    "unjudged_chunk_ids": unjudged,
                    "metrics": metrics,
                    "results": [
                        {
                            "rank": rank,
                            "chunk_id": item.chunk_id,
                            "doc_id": item.doc_id,
                            "source_path": item.source_path,
                            "heading_path": item.heading_path,
                            "score": item.score,
                            "relevance": chunk_relevance.get(item.chunk_id),
                        }
                        for rank, item in enumerate(ranked, start=1)
                    ],
                    "missed_relevant_chunk_ids": sorted(
                        chunk_id
                        for chunk_id, relevance in chunk_relevance.items()
                        if relevance > 0 and chunk_id not in retrieved_chunk_ids
                    ),
                    "missed_relevant_doc_ids": sorted(
                        doc_id
                        for doc_id, relevance in doc_relevance.items()
                        if relevance > 0 and doc_id not in retrieved_doc_ids
                    ),
                    "no_answer_diagnostic": (
                        {
                            "retrieved_count": len(ranked),
                            "top_score": ranked[0].score if ranked else None,
                            "note": (
                                "Retrieval has no abstention threshold; this records false-match "
                                "scores but does not claim no-answer accuracy."
                            ),
                        }
                        if not query.answerable
                        else None
                    ),
                }
            )
    finally:
        conn.close()

    latency_values = [float(result["latency_ms"]) for result in results]
    unjudged_total = sum(len(result["unjudged_chunk_ids"]) for result in results)
    categories = sorted({str(result["category"]) for result in results})
    return {
        "suite": "retrieval",
        "generated_at": datetime.now(UTC).isoformat(),
        "retriever": retriever,
        "index_version": index_version,
        "k_values": list(normalized_k),
        "query_count": len(results),
        "answerable_query_count": sum(bool(result["answerable"]) for result in results),
        "unanswerable_query_count": sum(not bool(result["answerable"]) for result in results),
        "judgment_complete": unjudged_total == 0,
        "unjudged_total": unjudged_total,
        "metrics": {
            "chunk": _macro_average(results, "chunk", normalized_k),
            "document": _macro_average(results, "document", normalized_k),
            "latency_ms": {
                "median": _nearest_rank(latency_values, 0.5),
                "p95": _nearest_rank(latency_values, 0.95),
                "max": max(latency_values) if latency_values else None,
            },
            "by_category": {
                category: {
                    "query_count": sum(result["category"] == category for result in results),
                    "chunk": _macro_average(
                        [result for result in results if result["category"] == category],
                        "chunk",
                        normalized_k,
                    ),
                    "document": _macro_average(
                        [result for result in results if result["category"] == category],
                        "document",
                        normalized_k,
                    ),
                }
                for category in categories
            },
        },
        "results": results,
    }
