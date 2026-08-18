from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from retrieval import dense as dense_module
from retrieval import search as search_module
from retrieval.dense import DenseSearcher, load_dense_searcher, rrf_fuse
from retrieval.indexer import build_index


def test_rrf_fuse_sums_reciprocal_ranks() -> None:
    fused = rrf_fuse(
        [
            [("shared", -1.2), ("fts_only", -0.8)],
            [("dense_only", 0.9), ("shared", 0.5)],
        ],
        top_k=5,
    )

    scores = dict(fused)
    assert scores["shared"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["dense_only"] == pytest.approx(1 / 61)
    assert scores["fts_only"] == pytest.approx(1 / 62)
    assert [chunk_id for chunk_id, _ in fused] == ["shared", "dense_only", "fts_only"]


def test_rrf_fuse_breaks_ties_on_chunk_id_and_truncates() -> None:
    fused = rrf_fuse([[("b", None)], [("a", None)]], top_k=1)

    # Both score exactly 1/61; the lexicographically smaller chunk_id wins so
    # two runs of the same query rank identically.
    assert fused == [("a", pytest.approx(1 / 61))]


def test_dense_searcher_fails_closed_on_missing_artifacts(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="missing.*chunk_mapping"):
        DenseSearcher(tmp_path)


def _fake_torch(*, cuda: bool = False, mps: bool = False):
    return SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: cuda),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
    )


def test_auto_device_prefers_cuda_then_mps_then_cpu() -> None:
    assert dense_module._resolve_device(_fake_torch(cuda=True, mps=True), "auto") == "cuda"
    assert dense_module._resolve_device(_fake_torch(mps=True), "auto") == "mps"
    assert dense_module._resolve_device(_fake_torch(), "auto") == "cpu"


def test_explicit_mps_fails_closed_when_unavailable() -> None:
    assert dense_module._resolve_device(_fake_torch(mps=True), "mps") == "mps"
    with pytest.raises(RuntimeError, match="Apple MPS is unavailable"):
        dense_module._resolve_device(_fake_torch(), "mps")


def test_unknown_dense_device_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="DENSE_DEVICE must be one of"):
        dense_module._resolve_device(_fake_torch(), "metal")


def _build_two_document_index(tmp_path: Path) -> Path:
    for name, value in (("data", "IMPORTANT VALUE"), ("unrelated", "DISTANT CONTENT")):
        (tmp_path / "corpus").mkdir(exist_ok=True)
        (tmp_path / "corpus" / f"{name}.html").write_text(
            f"""
            <html><head><title>{name}</title></head><body>
            <div class="header">{name}</div><div id="oContent">
              <table class="SubHeader3"><tr><td id="_Info">Info</td></tr></table>
              <table class="SubList"><tr><td>Field</td><td>{value}</td></tr></table>
            </div></body></html>
            """,
            encoding="utf-8",
        )
    db_path = tmp_path / "index.sqlite"
    build_index(tmp_path / "corpus", db_path, workers=1)
    return db_path


def _write_dense_artifact(dense_dir: Path, chunk_ids: list[str]) -> None:
    faiss = pytest.importorskip("faiss")
    np = pytest.importorskip("numpy")

    dense_dir.mkdir()
    vectors = np.zeros((len(chunk_ids), 2), dtype=np.float32)
    for position in range(len(chunk_ids)):
        vectors[position, position % 2] = 1.0
    index = faiss.IndexFlatIP(2)
    index.add(vectors)
    faiss.write_index(index, str(dense_dir / "corpus.faiss"))

    placeholder_hash = "0" * 64
    (dense_dir / "chunk_mapping.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "vector_position": position,
                    "chunk_id": chunk_id,
                    "source_file": "test.html",
                    "table_name": "TEST",
                    "column_name": None,
                    "chunk_type": "table_metadata",
                    "text_hash": placeholder_hash,
                }
            )
            + "\n"
            for position, chunk_id in enumerate(chunk_ids)
        ),
        encoding="utf-8",
    )
    (dense_dir / "index_metadata.json").write_text(
        json.dumps(
            {
                "index_version": "pretrained-flatip-v1",
                "model_name": "fake-model",
                "device": "cpu",
                "index_type": "IndexFlatIP",
                "normalized_embeddings": True,
                "embedding_dimension": 2,
                "source_chunk_count": len(chunk_ids),
                "indexed_chunk_count": len(chunk_ids),
                "requested_limit": None,
                "source_chunks_hash": placeholder_hash,
                "mapping_hash": placeholder_hash,
            }
        ),
        encoding="utf-8",
    )


class _FakeEncoder:
    model_name = "fake-model"
    device_name = "cpu"

    def encode_query(self, text: str):
        np = pytest.importorskip("numpy")
        del text
        return np.array([[1.0, 0.0]], dtype=np.float32)


@pytest.fixture(autouse=True)
def _clear_searcher_cache():
    dense_module._SEARCHERS.clear()
    yield
    dense_module._SEARCHERS.clear()


def test_hybrid_context_fuses_dense_only_document_into_results(tmp_path: Path) -> None:
    pytest.importorskip("faiss")
    db_path = _build_two_document_index(tmp_path)

    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    unrelated_chunk = str(
        conn.execute(
            """
            SELECT c.chunk_id FROM chunks AS c
            JOIN docs AS d ON d.doc_id = c.doc_id
            WHERE d.source_path LIKE '%unrelated.html' ORDER BY c.chunk_index
            """
        ).fetchone()["chunk_id"]
    )
    fts_top_chunk = str(
        search_module.search_ranked_chunks(conn.cursor(), query="important value", top_k=1)[0][
            "chunk_id"
        ]
    )
    conn.close()

    dense_dir = tmp_path / "dense"
    _write_dense_artifact(dense_dir, [unrelated_chunk, fts_top_chunk])
    load_dense_searcher(dense_dir, encoder=_FakeEncoder())

    context = search_module.retrieve_documentation_context(
        "important value", db_path, top_k=5, dense_index_dir=dense_dir
    )

    assert context["retrieval_mode"] == "hybrid"
    chunk_ids = [chunk["chunk_id"] for chunk in context["chunks"]]
    # FTS alone cannot reach unrelated.html (no shared token); the dense arm
    # is the only path to it, and the chunk present in both lists ranks first.
    assert chunk_ids[0] == fts_top_chunk
    assert unrelated_chunk in chunk_ids
    scores = {chunk["chunk_id"]: chunk["score"] for chunk in context["chunks"]}
    assert scores[fts_top_chunk] == pytest.approx(1 / 61 + 1 / 62)
    assert scores[unrelated_chunk] == pytest.approx(1 / 61)


def test_hybrid_fails_closed_when_artifact_chunk_is_not_in_index(tmp_path: Path) -> None:
    pytest.importorskip("faiss")
    db_path = _build_two_document_index(tmp_path)

    dense_dir = tmp_path / "dense"
    _write_dense_artifact(dense_dir, ["not-a-runtime-chunk-id", "also-missing"])
    load_dense_searcher(dense_dir, encoder=_FakeEncoder())

    with pytest.raises(RuntimeError, match="built against a different index"):
        search_module.retrieve_documentation_context(
            "important value", db_path, top_k=5, dense_index_dir=dense_dir
        )


def test_keyword_mode_is_untouched_without_dense_dir(tmp_path: Path) -> None:
    db_path = _build_two_document_index(tmp_path)

    context = search_module.retrieve_documentation_context("important value", db_path, top_k=5)

    assert context["retrieval_mode"] == "keyword"
    assert all("unrelated" not in chunk["source_path"] for chunk in context["chunks"])
