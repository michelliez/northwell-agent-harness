from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import faiss
import numpy as np
import pytest

from retrieval.genq.baseline_faiss import (
    BaselineConfig,
    _normalize_embeddings,
    run_baseline,
)

HASH = hashlib.sha256(b"value").hexdigest()


class FakeEncoder:
    model_name = "fake-semantic-model"
    device_name = "fake-mps"

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def encode(self, texts: Sequence[str], *, batch_size: int) -> np.ndarray:
        assert batch_size > 0
        return np.asarray([self.vectors[text] for text in texts], dtype=np.float32)


def chunk(chunk_id: str, text: str) -> dict[str, object]:
    table_name = chunk_id.split("__", maxsplit=1)[0]
    return {
        "chunk_id": chunk_id,
        "source_file": f"{table_name}.html",
        "source_hash": HASH,
        "table_name": table_name,
        "column_name": None,
        "chunk_type": "table_metadata",
        "section_name": "Table Metadata",
        "text": text,
        "text_hash": HASH,
        "parser_version": "epic-genq-html-v1",
    }


def query(
    query_id: str,
    text: str,
    relevant_chunk_id: str,
) -> dict[str, object]:
    table_name = relevant_chunk_id.split("__", maxsplit=1)[0]
    return {
        "query_id": query_id,
        "query": text,
        "relevant_chunk_id": relevant_chunk_id,
        "relevant_text_hash": HASH,
        "source_file": f"{table_name}.html",
        "chunk_type": "table_metadata",
        "split": "test",
        "split_version": "source-hash-split-v1",
        "generator_model": "BeIR/query-gen-msmarco-t5-large-v1",
        "generation_seed": 42,
        "query_index": 0,
        "filter_version": "deterministic-query-quality-v1",
        "meaningful_overlap_tokens": ["test"],
    }


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def config(
    tmp_path: Path,
    chunks: list[dict[str, object]],
    queries: list[dict[str, object]],
    **overrides: object,
) -> BaselineConfig:
    chunks_path = tmp_path / "chunks.jsonl"
    queries_path = tmp_path / "queries.jsonl"
    write_jsonl(chunks_path, chunks)
    write_jsonl(queries_path, queries)
    values: dict[str, object] = {
        "chunks_path": chunks_path,
        "queries_path": queries_path,
        "output_dir": tmp_path / "baseline",
        "limit": None,
        "batch_size": 2,
        "top_k": 3,
    }
    values.update(overrides)
    return BaselineConfig(**values)  # type: ignore[arg-type]


def test_exact_faiss_mapping_and_metrics(tmp_path: Path) -> None:
    chunks = [
        chunk("TABLE_A__METADATA", "passage a"),
        chunk("TABLE_B__METADATA", "passage b"),
        chunk("TABLE_C__METADATA", "passage c"),
    ]
    queries = [
        query("q_a", "query a", "TABLE_A__METADATA"),
        query("q_b", "query b", "TABLE_B__METADATA"),
    ]
    encoder = FakeEncoder(
        {
            "passage a": [1.0, 0.0],
            "passage b": [0.8, 0.6],
            "passage c": [0.0, 1.0],
            "query a": [1.0, 0.0],
            "query b": [1.0, 0.0],
        }
    )
    settings = config(tmp_path, chunks, queries)

    report = run_baseline(settings, encoder=encoder)

    assert report.evaluated_query_count == 2
    assert report.hit_at_1 == pytest.approx(0.5)
    assert report.hit_at_5 == pytest.approx(1.0)
    assert report.hit_at_10 == pytest.approx(1.0)
    assert report.mrr == pytest.approx(0.75)
    assert report.precision_at_1 == pytest.approx(0.5)
    assert report.precision_at_5 == pytest.approx(0.2)
    assert report.precision_at_10 == pytest.approx(0.1)
    assert report.recall_at_1 == pytest.approx(0.5)
    assert report.recall_at_5 == pytest.approx(1.0)
    assert report.recall_at_10 == pytest.approx(1.0)
    assert [result.positive_rank for result in report.query_results] == [1, 2]

    mapping = [
        json.loads(line)
        for line in (settings.output_dir / "chunk_mapping.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [(row["vector_position"], row["chunk_id"]) for row in mapping] == [
        (0, "TABLE_A__METADATA"),
        (1, "TABLE_B__METADATA"),
        (2, "TABLE_C__METADATA"),
    ]
    index = faiss.read_index(str(settings.output_dir / "corpus.faiss"))
    assert index.ntotal == 3
    assert index.d == 2


def test_embeddings_are_normalized_before_inner_product_search(tmp_path: Path) -> None:
    settings = config(
        tmp_path,
        [chunk("TABLE_A__METADATA", "passage a")],
        [query("q_a", "query a", "TABLE_A__METADATA")],
    )
    encoder = FakeEncoder({"passage a": [20.0, 0.0], "query a": [5.0, 0.0]})

    report = run_baseline(settings, encoder=encoder)

    assert report.query_results[0].top_hits[0].score == pytest.approx(1.0)


def test_bounded_index_must_include_every_evaluation_positive(tmp_path: Path) -> None:
    settings = config(
        tmp_path,
        [
            chunk("TABLE_A__METADATA", "passage a"),
            chunk("TABLE_B__METADATA", "passage b"),
        ],
        [query("q_b", "query b", "TABLE_B__METADATA")],
        limit=1,
    )

    with pytest.raises(ValueError, match="excludes positive chunks"):
        run_baseline(
            settings,
            encoder=FakeEncoder({"passage a": [1.0], "query b": [1.0]}),
        )
    assert not settings.output_dir.exists()


def test_query_provenance_mismatch_is_rejected(tmp_path: Path) -> None:
    bad_query = query("q_a", "query a", "TABLE_A__METADATA")
    bad_query["relevant_text_hash"] = hashlib.sha256(b"wrong").hexdigest()
    settings = config(
        tmp_path,
        [chunk("TABLE_A__METADATA", "passage a")],
        [bad_query],
    )

    with pytest.raises(ValueError, match="inconsistent chunk provenance"):
        run_baseline(
            settings,
            encoder=FakeEncoder({"passage a": [1.0], "query a": [1.0]}),
        )


@pytest.mark.parametrize(
    "vectors",
    [
        np.asarray([[0.0, 0.0]], dtype=np.float32),
        np.asarray([[float("nan"), 1.0]], dtype=np.float32),
        np.asarray([1.0, 2.0], dtype=np.float32),
    ],
)
def test_invalid_embeddings_are_rejected(vectors: np.ndarray) -> None:
    with pytest.raises(ValueError):
        _normalize_embeddings(vectors, expected_rows=1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"limit": 0},
        {"batch_size": 0},
        {"top_k": 0},
        {"device": "quantum"},
        {"model_name": " "},
    ],
)
def test_invalid_baseline_configuration_is_rejected(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    settings = config(
        tmp_path,
        [chunk("TABLE_A__METADATA", "passage a")],
        [query("q_a", "query a", "TABLE_A__METADATA")],
        **overrides,
    )

    with pytest.raises(ValueError):
        settings.validate()
