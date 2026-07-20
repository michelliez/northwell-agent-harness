from __future__ import annotations

from pydantic import BaseModel, Field

from agent_host.budget import ExecutionBudget
from agent_host.config import Settings
from agent_host.mcp_bridge import MCPToolBridge
from agent_host.tool_execution import call_workflow_tool
from agent_host.trace_logger import TraceLogger


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
    """Search the RAG MCP service and fetch bounded, fully cited chunks."""
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    bounded_top_k = budget.bound_retrieval_count(top_k)
    used_tools: list[str] = []

    async with MCPToolBridge(
        settings.rag_mcp_url,
        auth_token=getattr(settings, "mcp_auth_token", None),
    ) as rag_mcp:
        search_result = await call_workflow_tool(
            rag_mcp,
            "search_docs",
            {"query": query, "top_k": bounded_top_k},
            trace,
            used_tools,
            budget=budget,
            server="rag",
        )
        candidates = search_result.get("results", [])
        chunks: list[RetrievedChunk] = []
        for rank, candidate in enumerate(candidates[:bounded_top_k], start=1):
            chunk_id = candidate.get("chunk_id") if isinstance(candidate, dict) else None
            if not isinstance(chunk_id, str) or not chunk_id:
                continue
            chunk = await call_workflow_tool(
                rag_mcp,
                "get_doc_chunk",
                {"chunk_id": chunk_id},
                trace,
                used_tools,
                budget=budget,
                server="rag",
            )
            if chunk.get("error"):
                continue
            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk["chunk_id"],
                    document_id=chunk["doc_id"],
                    source_path=chunk["source_path"],
                    heading_path=chunk.get("heading_path"),
                    text=chunk["text"],
                    rank=rank,
                    score=candidate.get("score"),
                )
            )

    return RetrievalResult(
        query=query,
        chunks=chunks,
        index_version=search_result["index_version"],
    )


def resolve_schema_evidence(retrieval: RetrievalResult) -> SchemaSnapshot:
    """Resolve cited documentation into a schema snapshot for SQL validation.

    Not yet implemented — the RAG branch owns the extraction strategy. The
    explicit signature prevents the legacy fabricated catalog from becoming
    the implicit schema source for the future SQL workflow.
    """
    raise NotImplementedError(
        f"schema resolution requires the RAG branch (index={retrieval.index_version})"
    )
