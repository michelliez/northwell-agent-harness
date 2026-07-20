from __future__ import annotations

from pydantic import BaseModel, Field

from harness_spike.agent_host.budget import ExecutionBudget
from harness_spike.agent_host.trace_logger import TraceLogger
from harness_spike.config import Settings


class RetrievedChunk(BaseModel):
    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    heading_path: str | None = None
    text: str = Field(min_length=1)
    rank: int = Field(ge=1)
    score: float | None = None


class RetrievalResult(BaseModel):
    query: str = Field(min_length=1)
    chunks: list[RetrievedChunk]
    index_version: str = Field(min_length=1)


class SchemaColumn(BaseModel):
    name: str = Field(min_length=1)
    data_type: str | None = None
    source_chunk_ids: list[str] = Field(min_length=1)


class SchemaTable(BaseModel):
    name: str = Field(min_length=1)
    columns: list[SchemaColumn]
    source_chunk_ids: list[str] = Field(min_length=1)


class SchemaSnapshot(BaseModel):
    """Schema evidence derived from retrieval results, used by the SQL workflow."""

    tables: list[SchemaTable]
    index_version: str = Field(min_length=1)


async def retrieve_documentation(
    query: str,
    settings: Settings,
    trace: TraceLogger,
    *,
    budget: ExecutionBudget,
    top_k: int = 5,
) -> RetrievalResult:
    """Retrieve documentation chunks relevant to a query.

    Not yet implemented — requires the RAG index integration branch.
    """
    if not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20")
    raise NotImplementedError("retrieve_documentation requires the RAG index branch")


def resolve_schema_evidence(retrieval: RetrievalResult) -> SchemaSnapshot:
    """Resolve cited documentation into a schema snapshot for SQL validation.

    Not yet implemented — the RAG branch owns the extraction strategy. The
    explicit signature prevents the legacy fabricated catalog from becoming
    the implicit schema source for the future SQL workflow.
    """
    raise NotImplementedError(
        f"schema resolution requires the RAG branch (index={retrieval.index_version})"
    )
