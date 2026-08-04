"""Validated records emitted by the Stage 1 Epic HTML corpus parser."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ChunkType = Literal[
    "table_metadata",
    "column_definition",
    "primary_key",
    "index_information",
    "foreign_key",
    "relationship",
    "section",
]
SplitName = Literal["train", "validation", "test"]
FilterDecision = Literal["retain", "reject", "review"]


class ChunkRecord(BaseModel):
    """One self-contained retrieval passage with stable source metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1, max_length=512)
    source_file: str = Field(min_length=1)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    table_name: str = Field(min_length=1)
    column_name: str | None = None
    chunk_type: ChunkType
    section_name: str = Field(min_length=1)
    text: str = Field(min_length=1)
    text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: str = Field(min_length=1)

    @field_validator("chunk_id", "source_file", "table_name", "section_name", "text")
    @classmethod
    def reject_surrounding_whitespace(cls, value: str) -> str:
        """Reject ambiguous records instead of silently changing their identity."""
        if value != value.strip():
            raise ValueError("must not contain surrounding whitespace")
        return value


class ParseReport(BaseModel):
    """Summary and inspection signals for one deterministic parser run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parser_version: str
    input_path: str
    output_path: str
    requested_limit: int | None
    source_file_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    chunk_counts_by_type: dict[str, int]
    unavailable_section_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    warnings: list[str]
    corpus_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SplitChunkRecord(ChunkRecord):
    """A Stage 1 chunk with its leakage-safe source-group assignment."""

    split: SplitName
    split_version: str = Field(min_length=1)


class SplitReport(BaseModel):
    """Audit report proving how groups and chunks were assigned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    split_version: str
    input_path: str
    output_path: str
    seed: str = Field(min_length=1)
    ratios: dict[SplitName, float]
    source_file_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    source_counts_by_split: dict[SplitName, int]
    chunk_counts_by_split: dict[SplitName, int]
    chunk_type_counts_by_split: dict[SplitName, dict[str, int]]
    leakage_source_count: int = Field(ge=0)
    duplicate_chunk_id_count: int = Field(ge=0)
    input_corpus_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_corpus_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    split_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GeneratedQueryRecord(BaseModel):
    """One raw synthetic query paired with its known source passage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    relevant_chunk_id: str = Field(min_length=1)
    relevant_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_file: str = Field(min_length=1)
    chunk_type: ChunkType
    split: SplitName
    split_version: str = Field(min_length=1)
    generator_model: str = Field(min_length=1)
    generation_seed: int = Field(ge=0)
    query_index: int = Field(ge=0)

    @field_validator("query_id", "query", "relevant_chunk_id", "source_file")
    @classmethod
    def reject_query_whitespace(cls, value: str) -> str:
        """Keep query identity and later duplicate checks unambiguous."""
        if value != value.strip():
            raise ValueError("must not contain surrounding whitespace")
        return value


class GenerationReport(BaseModel):
    """Configuration and counts for one raw synthetic-query generation run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    generation_version: str
    input_path: str
    output_path: str
    generator_model: str
    device: str
    seed: int = Field(ge=0)
    batch_size: int = Field(ge=1)
    queries_per_chunk: int = Field(ge=1)
    max_input_tokens: int = Field(ge=1)
    max_query_tokens: int = Field(ge=1)
    top_p: float = Field(gt=0, le=1)
    min_passage_chars: int = Field(ge=0)
    requested_limit: int | None
    input_chunk_count: int = Field(ge=0)
    eligible_chunk_count: int = Field(ge=0)
    selected_chunk_count: int = Field(ge=0)
    skipped_short_chunk_count: int = Field(ge=0)
    failed_generation_chunk_count: int = Field(ge=0)
    generated_query_count: int = Field(ge=0)
    duplicate_query_count: int = Field(ge=0)
    query_counts_by_split: dict[SplitName, int]
    query_counts_by_chunk_type: dict[str, int]
    input_corpus_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_query_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class FilteredQueryRecord(GeneratedQueryRecord):
    """A raw query that passed deterministic Stage 4 quality gates."""

    filter_version: str = Field(min_length=1)
    meaningful_overlap_tokens: list[str]


class QueryReviewRecord(GeneratedQueryRecord):
    """Auditable decision for every raw query, including rejected candidates."""

    filter_version: str = Field(min_length=1)
    decision: FilterDecision
    reason_codes: list[str]
    normalized_query: str = Field(min_length=1)
    meaningful_overlap_tokens: list[str]
    near_duplicate_of: str | None = None


class FilterReport(BaseModel):
    """Counts, configuration, and hashes for a Stage 4 filtering run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    filter_version: str
    queries_path: str
    chunks_path: str
    retained_output_path: str
    review_output_path: str
    min_query_words: int = Field(ge=1)
    max_query_words: int = Field(ge=1)
    max_query_chars: int = Field(ge=1)
    near_duplicate_threshold: float = Field(gt=0, le=1)
    passage_copy_threshold: float = Field(gt=0, le=1)
    input_query_count: int = Field(ge=0)
    retained_query_count: int = Field(ge=0)
    rejected_query_count: int = Field(ge=0)
    manual_review_query_count: int = Field(ge=0)
    decision_counts: dict[FilterDecision, int]
    reason_counts: dict[str, int]
    retained_counts_by_split: dict[SplitName, int]
    raw_queries_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_chunks_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    retained_queries_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_ledger_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class FaissMappingRecord(BaseModel):
    """Exact relationship between one FAISS vector position and a chunk."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    vector_position: int = Field(ge=0)
    chunk_id: str = Field(min_length=1)
    source_file: str = Field(min_length=1)
    table_name: str = Field(min_length=1)
    column_name: str | None = None
    chunk_type: ChunkType
    text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class BaselineIndexMetadata(BaseModel):
    """Reproducibility contract for an exact pretrained-model FAISS index."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    index_version: str
    model_name: str
    device: str
    index_type: Literal["IndexFlatIP"]
    normalized_embeddings: Literal[True]
    embedding_dimension: int = Field(ge=1)
    source_chunk_count: int = Field(ge=1)
    indexed_chunk_count: int = Field(ge=1)
    requested_limit: int | None
    source_chunks_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RankedChunkHit(BaseModel):
    """One ranked result retained for baseline-evaluation inspection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(ge=1)
    score: float
    chunk_id: str = Field(min_length=1)
    source_file: str = Field(min_length=1)
    table_name: str = Field(min_length=1)
    column_name: str | None = None
    chunk_type: ChunkType


class BaselineQueryResult(BaseModel):
    """Exact rank and metrics for one known-positive synthetic query."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: str
    query: str
    relevant_chunk_id: str
    positive_rank: int = Field(ge=1)
    reciprocal_rank: float = Field(gt=0, le=1)
    precision_at_1: float = Field(ge=0, le=1)
    precision_at_5: float = Field(ge=0, le=1)
    precision_at_10: float = Field(ge=0, le=1)
    recall_at_1: float = Field(ge=0, le=1)
    recall_at_5: float = Field(ge=0, le=1)
    recall_at_10: float = Field(ge=0, le=1)
    hit_at_1: float = Field(ge=0, le=1)
    hit_at_5: float = Field(ge=0, le=1)
    hit_at_10: float = Field(ge=0, le=1)
    ndcg_at_10: float = Field(ge=0, le=1)
    top_hits: list[RankedChunkHit]


class BaselineEvaluationReport(BaseModel):
    """Aggregate exact-retrieval metrics for an unfine-tuned encoder."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluation_version: str
    model_name: str
    device: str
    index_version: str
    candidate_chunk_count: int = Field(ge=1)
    evaluated_query_count: int = Field(ge=1)
    precision_at_1: float = Field(ge=0, le=1)
    precision_at_5: float = Field(ge=0, le=1)
    precision_at_10: float = Field(ge=0, le=1)
    recall_at_1: float = Field(ge=0, le=1)
    recall_at_5: float = Field(ge=0, le=1)
    recall_at_10: float = Field(ge=0, le=1)
    hit_at_1: float = Field(ge=0, le=1)
    hit_at_5: float = Field(ge=0, le=1)
    hit_at_10: float = Field(ge=0, le=1)
    mrr: float = Field(ge=0, le=1)
    ndcg_at_10: float = Field(ge=0, le=1)
    retained_queries_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    query_results: list[BaselineQueryResult]
