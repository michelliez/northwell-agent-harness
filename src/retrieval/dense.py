"""Dense retrieval over a prebuilt FAISS artifact, fused with FTS by RRF.

The corpus is embedded offline (the artifact directory holds ``corpus.faiss``,
``chunk_mapping.jsonl``, and ``index_metadata.json`` keyed by runtime chunk
IDs); the request path only encodes the query. Fusion is reciprocal-rank:
each arm contributes its top ``top_k * HYBRID_CANDIDATE_MULTIPLIER`` chunks
and a chunk's fused score is the sum of ``1 / (RRF_K + rank)`` over the lists
that contain it. Rank positions carry the signal rather than raw scores, so
BM25 and inner-product scales never need calibrating against each other.

The constants are frozen from the evaluation repository's measured hybrid arm
(gold-85 benchmark, 2026-08-10): fusion held every FTS identifier bucket
exactly while tripling business-concept hit@5. Change them only with a rerun
of that benchmark in hand.

Dependencies (torch, transformers, faiss, numpy) belong to the optional
``dense`` group and are imported lazily; everything fails closed with an
actionable message when the group is absent, mirroring the BigQuery adapter.
"""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from retrieval.chunk_models import BaselineIndexMetadata, FaissMappingRecord

RRF_K = 60
HYBRID_CANDIDATE_MULTIPLIER = 2

# Must match the instruction the artifact's queries were evaluated with; the
# encoder is asymmetric and a drifting task string silently shifts the query
# embedding space away from the benchmark that justified shipping this.
QUERY_TASK = (
    "Given a question about an Epic Clarity database table or column, "
    "retrieve the data-dictionary passage that answers it"
)
QUERY_MAX_LENGTH = 512
SUPPORTED_DENSE_DEVICES = frozenset({"auto", "cpu", "cuda", "mps"})


def _resolve_device(torch: Any, requested: str) -> str:
    """Resolve and validate the query-encoder device.

    CUDA remains preferred where available. Apple Silicon now uses MPS before
    falling back to CPU, and operators can pin any supported backend with
    ``DENSE_DEVICE``.
    """
    normalized = requested.strip().lower()
    if normalized not in SUPPORTED_DENSE_DEVICES:
        choices = ", ".join(sorted(SUPPORTED_DENSE_DEVICES))
        raise RuntimeError(f"DENSE_DEVICE must be one of: {choices}")

    cuda_available = bool(torch.cuda.is_available())
    mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
    mps_available = bool(mps_backend is not None and mps_backend.is_available())

    if normalized == "auto":
        if cuda_available:
            return "cuda"
        if mps_available:
            return "mps"
        return "cpu"
    if normalized == "cuda" and not cuda_available:
        raise RuntimeError("DENSE_DEVICE=cuda was requested but CUDA is unavailable")
    if normalized == "mps" and not mps_available:
        raise RuntimeError("DENSE_DEVICE=mps was requested but Apple MPS is unavailable")
    return normalized


class QueryEncoder(Protocol):
    """Encodes one query into a single L2-normalized embedding row."""

    model_name: str
    device_name: str

    def encode_query(self, text: str) -> Any:
        """Return a float32 array of shape (1, dimension) with unit norm."""
        ...


class QwenQueryEncoder:
    """Query-side port of the eval repository's Qwen3-Embedding encoder.

    Qwen3-Embedding is a causal model trained so the final token's hidden
    state carries the sequence embedding, and it is asymmetric: queries are
    wrapped in an instruction while documents were embedded bare offline.
    Mean pooling or a missing instruction produces embeddings that look valid
    and retrieve poorly, so this class refuses nothing at call time -- the
    checkpoint-family check happens in ``DenseSearcher``.
    """

    def __init__(self, model_name: str, device: str = "auto") -> None:
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Dense retrieval dependencies are missing. Run `uv sync --group dense`."
            ) from exc

        self._torch = torch
        self.model_name = model_name
        device = _resolve_device(torch, device)
        self.device_name = device
        self._tokenizer = AutoTokenizer.from_pretrained(model_name, padding_side="left")
        self._model = AutoModel.from_pretrained(
            model_name,
            # MPS and CUDA both support half precision and avoid expanding the
            # 0.6B checkpoint to a multi-gigabyte float32 CPU copy.
            dtype=torch.float16 if device in {"cuda", "mps"} else torch.float32,
            low_cpu_mem_usage=True,
        )
        self._model.eval()
        self._model.to(device)

    def encode_query(self, text: str) -> Any:
        import numpy as np

        prompt = f"Instruct: {QUERY_TASK}\nQuery:{text}"
        tokenized = self._tokenizer(
            [prompt],
            padding=True,
            truncation=True,
            max_length=QUERY_MAX_LENGTH,
            return_tensors="pt",
        ).to(self.device_name)
        with self._torch.inference_mode():
            hidden = self._model(**tokenized).last_hidden_state
            # Left padding guarantees the final column is a real token.
            pooled = hidden[:, -1]
        vector = pooled.detach().cpu().float().numpy().astype(np.float32)
        norms = np.linalg.norm(vector, axis=1, keepdims=True)
        if not np.isfinite(vector).all() or np.any(norms == 0):
            raise RuntimeError("Query encoder returned a non-finite or zero-length embedding")
        return np.ascontiguousarray(vector / norms, dtype=np.float32)


class DenseSearcher:
    """A loaded FAISS artifact plus its query encoder."""

    def __init__(self, index_dir: Path, *, encoder: QueryEncoder | None = None) -> None:
        index_path = index_dir / "corpus.faiss"
        mapping_path = index_dir / "chunk_mapping.jsonl"
        metadata_path = index_dir / "index_metadata.json"
        missing = sorted(
            path.name for path in (index_path, mapping_path, metadata_path) if not path.is_file()
        )
        if missing:
            raise RuntimeError(
                f"Dense index directory {index_dir} is missing {', '.join(missing)}; "
                "build the artifact against the active RAG index first."
            )

        self.metadata = BaselineIndexMetadata.model_validate_json(
            metadata_path.read_text(encoding="utf-8")
        )
        self._chunk_id_by_position: dict[int, str] = {}
        for line in mapping_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = FaissMappingRecord.model_validate_json(line)
            self._chunk_id_by_position[record.vector_position] = record.chunk_id
        if not self._chunk_id_by_position:
            raise RuntimeError(f"{mapping_path}: no mapping records found")

        try:
            import faiss  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise RuntimeError(
                "Dense retrieval dependencies are missing. Run `uv sync --group dense`."
            ) from exc
        self._index = faiss.read_index(str(index_path))
        if self._index.ntotal != len(self._chunk_id_by_position):
            raise RuntimeError(
                f"FAISS index holds {self._index.ntotal} vectors but the mapping lists "
                f"{len(self._chunk_id_by_position)}"
            )

        if encoder is None:
            if "qwen3-embedding" not in self.metadata.model_name.casefold():
                raise RuntimeError(
                    f"Dense artifact was built with {self.metadata.model_name!r}; only "
                    "Qwen3-Embedding checkpoints are supported in the request path."
                )
            encoder = QwenQueryEncoder(
                self.metadata.model_name,
                device=os.getenv("DENSE_DEVICE", "auto"),
            )
        self._encoder = encoder

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        query_vector = self._encoder.encode_query(query)
        scores, positions = self._index.search(query_vector, min(top_k, self._index.ntotal))
        return [
            (self._chunk_id_by_position[int(position)], float(score))
            for position, score in zip(positions[0], scores[0], strict=True)
            if int(position) >= 0
        ]


# One artifact load and one resident encoder per process, keyed by directory.
_SEARCHERS: dict[str, DenseSearcher] = {}


def load_dense_searcher(index_dir: Path, *, encoder: QueryEncoder | None = None) -> DenseSearcher:
    # Device configuration is startup configuration; restart the process after
    # changing DENSE_DEVICE so the resident encoder is rebuilt on that backend.
    key = str(index_dir.resolve())
    if key not in _SEARCHERS:
        _SEARCHERS[key] = DenseSearcher(index_dir, encoder=encoder)
    return _SEARCHERS[key]


def rrf_fuse(
    ranked_lists: Sequence[Sequence[tuple[str, float | None]]],
    top_k: int,
) -> list[tuple[str, float]]:
    """Fuse ranked (chunk_id, score) lists by reciprocal rank.

    Input scores are ignored -- only positions matter. Ties break on chunk_id
    so two runs of the same query rank identically.
    """
    fused: dict[str, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, (chunk_id, _score) in enumerate(ranked, start=1):
            fused[chunk_id] += 1.0 / (RRF_K + rank)
    ordered = sorted(fused.items(), key=lambda pair: (-pair[1], pair[0]))
    return ordered[:top_k]


__all__ = [
    "HYBRID_CANDIDATE_MULTIPLIER",
    "RRF_K",
    "SUPPORTED_DENSE_DEVICES",
    "DenseSearcher",
    "QueryEncoder",
    "QwenQueryEncoder",
    "load_dense_searcher",
    "rrf_fuse",
]
