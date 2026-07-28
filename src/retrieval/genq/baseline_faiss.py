"""Build and evaluate an exact FAISS baseline without model training."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np  # pyright: ignore[reportMissingImports]
from pydantic import BaseModel, ValidationError

from evals.retrieval_evaluator import ranked_metrics
from retrieval.genq.chunk_models import (
    BaselineEvaluationReport,
    BaselineIndexMetadata,
    BaselineQueryResult,
    ChunkRecord,
    FaissMappingRecord,
    FilteredQueryRecord,
    RankedChunkHit,
)

LOGGER = logging.getLogger(__name__)
INDEX_VERSION = "pretrained-flatip-v1"
EVALUATION_VERSION = "known-positive-exact-rank-v1"
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_LIMIT = 200
DEFAULT_BATCH_SIZE = 32
DEFAULT_TOP_K = 10
DEVICE_CHOICES = ("auto", "cpu", "cuda", "mps")


class TextEncoder(Protocol):
    """Minimal encoder interface used by production code and lightweight tests."""

    model_name: str
    device_name: str

    def encode(self, texts: Sequence[str], *, batch_size: int) -> np.ndarray:
        """Return one finite embedding row per input text."""
        ...


@dataclass(frozen=True)
class BaselineConfig:
    """Configuration for a bounded pretrained-model FAISS evaluation."""

    chunks_path: Path
    queries_path: Path
    output_dir: Path
    model_name: str = DEFAULT_MODEL
    device: str = "auto"
    limit: int | None = DEFAULT_LIMIT
    batch_size: int = DEFAULT_BATCH_SIZE
    top_k: int = DEFAULT_TOP_K

    def validate(self) -> None:
        """Reject unsafe output placement and nonsensical runtime settings."""
        if self.output_dir in {self.chunks_path, self.queries_path}:
            raise ValueError("output_dir must differ from both input paths")
        if not self.model_name.strip():
            raise ValueError("model_name must not be blank")
        if self.device not in DEVICE_CHOICES:
            raise ValueError(f"device must be one of {DEVICE_CHOICES}")
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be at least 1 or None")
        if self.batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if self.top_k < 1:
            raise ValueError("top_k must be at least 1")


class SentenceTransformerEncoder:
    """Load a mean-pooled SentenceTransformer checkpoint without optional utilities.

    Importing the top-level ``sentence_transformers`` package also imports
    scikit-learn and SciPy. For the MiniLM baseline we only need its Transformer
    weights plus the standard attention-mask mean pooling used by the model.
    """

    def __init__(self, model_name: str, device: str) -> None:
        try:
            import torch  # pyright: ignore[reportMissingImports]
            from transformers import (  # pyright: ignore[reportMissingImports]
                AutoModel,
                AutoTokenizer,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Baseline dependencies are missing. Run `uv sync --all-groups`."
            ) from exc

        self._torch = torch
        self.model_name = model_name
        self.device_name = self._resolve_device(torch, device)
        LOGGER.info("Loading %s on %s", model_name, self.device_name)
        self._tokenizer = AutoTokenizer.from_pretrained(model_name)
        self._model = AutoModel.from_pretrained(model_name)
        self._model.eval()
        self._model.to(self.device_name)

    @staticmethod
    def _resolve_device(torch: object, requested: str) -> str:
        cuda_available = bool(torch.cuda.is_available())  # type: ignore[attr-defined]
        mps_available = bool(torch.backends.mps.is_available())  # type: ignore[attr-defined]
        if requested == "cuda" and not cuda_available:
            raise ValueError("CUDA was requested but is not available")
        if requested == "mps" and not mps_available:
            raise ValueError("MPS was requested but is not available")
        if requested != "auto":
            return requested
        if cuda_available:
            return "cuda"
        if mps_available:
            return "mps"
        return "cpu"

    def encode(self, texts: Sequence[str], *, batch_size: int) -> np.ndarray:
        rows: list[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            batch = list(texts[start : start + batch_size])
            LOGGER.info(
                "Embedding %d/%d texts",
                min(start + len(batch), len(texts)),
                len(texts),
            )
            tokenized = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                return_tensors="pt",
            ).to(self.device_name)
            with self._torch.inference_mode():
                hidden = self._model(**tokenized).last_hidden_state
                attention_mask = tokenized["attention_mask"].unsqueeze(-1)
                masked_hidden = hidden * attention_mask
                pooled = masked_hidden.sum(dim=1) / attention_mask.sum(dim=1).clamp(min=1)
            rows.append(pooled.detach().cpu().numpy().astype(np.float32))
        return np.concatenate(rows, axis=0)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _load_jsonl[T: BaseModel](path: Path, model: type[T], label: str) -> tuple[list[T], str]:
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(f"{label} JSONL does not exist: {path}") from exc
    if not raw_bytes.strip():
        raise ValueError(f"{label} JSONL is empty: {path}")
    records: list[T] = []
    for line_number, line in enumerate(raw_bytes.splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"{path}:{line_number}: blank JSONL line")
        try:
            records.append(model.model_validate_json(line))
        except ValidationError as exc:
            raise ValueError(f"{path}:{line_number}: invalid {label} record: {exc}") from exc
    return records, _sha256_bytes(raw_bytes)


def _normalize_embeddings(embeddings: np.ndarray, *, expected_rows: int) -> np.ndarray:
    if embeddings.ndim != 2 or embeddings.shape[0] != expected_rows:
        raise ValueError(
            f"Encoder returned shape {embeddings.shape}; expected ({expected_rows}, dimension)"
        )
    if embeddings.shape[1] < 1:
        raise ValueError("Encoder returned zero-dimensional embeddings")
    if not np.isfinite(embeddings).all():
        raise ValueError("Encoder returned non-finite embedding values")
    vectors = np.asarray(embeddings, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("Encoder returned a zero-length embedding")
    return np.ascontiguousarray(vectors / norms, dtype=np.float32)


def _write_atomic(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            for line in lines:
                output.write(line)
                output.write("\n")
        Path(temporary_name).replace(path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _write_faiss_atomic(path: Path, index: Any) -> None:
    import faiss  # pyright: ignore[reportMissingImports]

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".faiss"
    )
    os.close(descriptor)
    try:
        faiss.write_index(index, temporary_name)
        Path(temporary_name).replace(path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _validate_inputs(
    chunks: list[ChunkRecord],
    queries: list[FilteredQueryRecord],
) -> dict[str, ChunkRecord]:
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    if len(chunk_by_id) != len(chunks):
        raise ValueError("Duplicate chunk IDs found")
    query_ids = Counter(query.query_id for query in queries)
    duplicates = [query_id for query_id, count in query_ids.items() if count > 1]
    if duplicates:
        raise ValueError(f"Duplicate query IDs found: {', '.join(sorted(duplicates)[:5])}")
    for query in queries:
        chunk = chunk_by_id.get(query.relevant_chunk_id)
        if chunk is None:
            raise ValueError(
                f"Query {query.query_id} references missing chunk {query.relevant_chunk_id}"
            )
        if (
            query.relevant_text_hash != chunk.text_hash
            or query.source_file != chunk.source_file
            or query.chunk_type != chunk.chunk_type
        ):
            raise ValueError(f"Query {query.query_id} has inconsistent chunk provenance")
    return chunk_by_id


def _required_metric(
    metrics: dict[str, float | bool | None],
    name: str,
) -> float:
    """Return a numeric metric that must exist for known-positive evaluation."""
    value = metrics[name]
    if value is None:
        raise RuntimeError(f"Shared ranking calculator returned no value for {name}")
    return float(value)


def run_baseline(
    config: BaselineConfig,
    *,
    encoder: TextEncoder | None = None,
) -> BaselineEvaluationReport:
    """Embed chunks, build exact FAISS, and evaluate known-positive query ranks."""
    config.validate()
    all_chunks, chunks_hash = _load_jsonl(config.chunks_path, ChunkRecord, "chunk")
    queries, queries_hash = _load_jsonl(config.queries_path, FilteredQueryRecord, "retained query")
    _validate_inputs(all_chunks, queries)
    selected = all_chunks[: config.limit] if config.limit is not None else all_chunks
    selected_ids = {chunk.chunk_id for chunk in selected}
    missing_positives = sorted({query.relevant_chunk_id for query in queries} - selected_ids)
    if missing_positives:
        raise ValueError(
            "The bounded index excludes positive chunks required by evaluation: "
            + ", ".join(missing_positives[:5])
        )

    active_encoder = encoder or SentenceTransformerEncoder(config.model_name, config.device)
    chunk_vectors = _normalize_embeddings(
        active_encoder.encode([chunk.text for chunk in selected], batch_size=config.batch_size),
        expected_rows=len(selected),
    )
    query_vectors = _normalize_embeddings(
        active_encoder.encode([query.query for query in queries], batch_size=config.batch_size),
        expected_rows=len(queries),
    )

    import faiss  # pyright: ignore[reportMissingImports]

    dimension = int(chunk_vectors.shape[1])
    if query_vectors.shape[1] != dimension:
        raise ValueError("Query and chunk embedding dimensions do not match")
    index = faiss.IndexFlatIP(dimension)
    index.add(chunk_vectors)
    if index.ntotal != len(selected):
        raise RuntimeError("FAISS index size does not match selected chunk count")

    mappings = [
        FaissMappingRecord(
            vector_position=position,
            chunk_id=chunk.chunk_id,
            source_file=chunk.source_file,
            table_name=chunk.table_name,
            column_name=chunk.column_name,
            chunk_type=chunk.chunk_type,
            text_hash=chunk.text_hash,
        )
        for position, chunk in enumerate(selected)
    ]
    mapping_lines = [
        json.dumps(mapping.model_dump(), sort_keys=True, ensure_ascii=False) for mapping in mappings
    ]
    mapping_hash = _sha256_text("\n".join(mapping_lines) + "\n")
    metadata = BaselineIndexMetadata(
        index_version=INDEX_VERSION,
        model_name=active_encoder.model_name,
        device=active_encoder.device_name,
        index_type="IndexFlatIP",
        normalized_embeddings=True,
        embedding_dimension=dimension,
        source_chunk_count=len(all_chunks),
        indexed_chunk_count=len(selected),
        requested_limit=config.limit,
        source_chunks_hash=chunks_hash,
        mapping_hash=mapping_hash,
    )

    scores, positions = index.search(query_vectors, len(selected))
    mapping_by_position = {mapping.vector_position: mapping for mapping in mappings}
    query_results: list[BaselineQueryResult] = []
    inspection_k = min(config.top_k, len(selected))
    for query_index, query in enumerate(queries):
        ranked_positions = [int(value) for value in positions[query_index]]
        positive_position = next(
            mapping.vector_position
            for mapping in mappings
            if mapping.chunk_id == query.relevant_chunk_id
        )
        positive_rank = ranked_positions.index(positive_position) + 1
        ranked_chunk_ids = [mapping_by_position[position].chunk_id for position in ranked_positions]
        metrics = ranked_metrics(
            ranked_chunk_ids,
            {query.relevant_chunk_id: 1},
            (1, 5, 10),
        )
        top_hits: list[RankedChunkHit] = []
        for rank, vector_position in enumerate(ranked_positions[:inspection_k], start=1):
            mapping = mapping_by_position[vector_position]
            top_hits.append(
                RankedChunkHit(
                    rank=rank,
                    score=float(scores[query_index, rank - 1]),
                    chunk_id=mapping.chunk_id,
                    source_file=mapping.source_file,
                    table_name=mapping.table_name,
                    column_name=mapping.column_name,
                    chunk_type=mapping.chunk_type,
                )
            )
        query_results.append(
            BaselineQueryResult(
                query_id=query.query_id,
                query=query.query,
                relevant_chunk_id=query.relevant_chunk_id,
                positive_rank=positive_rank,
                reciprocal_rank=_required_metric(metrics, "mrr"),
                precision_at_1=_required_metric(metrics, "precision@1"),
                precision_at_5=_required_metric(metrics, "precision@5"),
                precision_at_10=_required_metric(metrics, "precision@10"),
                recall_at_1=_required_metric(metrics, "recall@1"),
                recall_at_5=_required_metric(metrics, "recall@5"),
                recall_at_10=_required_metric(metrics, "recall@10"),
                hit_at_1=_required_metric(metrics, "hit@1"),
                hit_at_5=_required_metric(metrics, "hit@5"),
                hit_at_10=_required_metric(metrics, "hit@10"),
                ndcg_at_10=_required_metric(metrics, "ndcg@10"),
                top_hits=top_hits,
            )
        )

    count = len(query_results)
    report = BaselineEvaluationReport(
        evaluation_version=EVALUATION_VERSION,
        model_name=active_encoder.model_name,
        device=active_encoder.device_name,
        index_version=INDEX_VERSION,
        candidate_chunk_count=len(selected),
        evaluated_query_count=count,
        precision_at_1=sum(result.precision_at_1 for result in query_results) / count,
        precision_at_5=sum(result.precision_at_5 for result in query_results) / count,
        precision_at_10=sum(result.precision_at_10 for result in query_results) / count,
        recall_at_1=sum(result.recall_at_1 for result in query_results) / count,
        recall_at_5=sum(result.recall_at_5 for result in query_results) / count,
        recall_at_10=sum(result.recall_at_10 for result in query_results) / count,
        hit_at_1=sum(result.hit_at_1 for result in query_results) / count,
        hit_at_5=sum(result.hit_at_5 for result in query_results) / count,
        hit_at_10=sum(result.hit_at_10 for result in query_results) / count,
        mrr=sum(result.reciprocal_rank for result in query_results) / count,
        ndcg_at_10=sum(result.ndcg_at_10 for result in query_results) / count,
        retained_queries_hash=queries_hash,
        query_results=query_results,
    )

    config.output_dir.mkdir(parents=True, exist_ok=True)
    _write_faiss_atomic(config.output_dir / "corpus.faiss", index)
    _write_atomic(config.output_dir / "chunk_mapping.jsonl", mapping_lines)
    _write_atomic(
        config.output_dir / "index_metadata.json",
        [json.dumps(metadata.model_dump(), indent=2, sort_keys=True)],
    )
    _write_atomic(
        config.output_dir / "smoke_evaluation.json",
        [json.dumps(report.model_dump(), indent=2, sort_keys=True)],
    )
    return report


def main() -> None:
    """Run the bounded pretrained baseline index and evaluation."""
    parser = argparse.ArgumentParser(
        description="Build exact pretrained FAISS and evaluate retained queries."
    )
    parser.add_argument("chunks_path", type=Path)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--device", choices=DEVICE_CHOICES, default="auto")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        report = run_baseline(
            BaselineConfig(
                chunks_path=args.chunks_path,
                queries_path=args.queries,
                output_dir=args.output_dir,
                model_name=args.model,
                device=args.device,
                limit=args.limit,
                batch_size=args.batch_size,
                top_k=args.top_k,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"Evaluated {report.evaluated_query_count} query against "
        f"{report.candidate_chunk_count} exact FAISS candidates: "
        f"Hit@1={report.hit_at_1:.3f} Hit@5={report.hit_at_5:.3f} "
        f"Hit@10={report.hit_at_10:.3f} MRR={report.mrr:.3f}"
    )


if __name__ == "__main__":
    main()
