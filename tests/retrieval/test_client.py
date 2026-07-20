from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agent_host.budget import ExecutionBudget
from agent_host.trace_logger import TraceLogger
from retrieval.client import (
    RetrievedChunk,
    SchemaColumn,
    SchemaTable,
    retrieve_documentation,
)


def test_retrieved_chunk_requires_stable_source_identity() -> None:
    with pytest.raises(ValidationError):
        RetrievedChunk(
            chunk_id="",
            document_id="appointments",
            source_path="approved/appointments.html",
            text="Appointment status documentation.",
            rank=1,
        )

    with pytest.raises(ValidationError):
        RetrievedChunk(
            chunk_id="appointments-status",
            document_id="appointments",
            source_path="approved/appointments.html",
            text="Appointment status documentation.",
            rank=0,
        )


def test_schema_evidence_requires_chunk_provenance() -> None:
    with pytest.raises(ValidationError):
        SchemaTable(
            name="appointments",
            columns=[
                SchemaColumn(
                    name="status",
                    source_chunk_ids=["appointments-status"],
                )
            ],
            source_chunk_ids=[],
        )


@pytest.mark.asyncio
async def test_retrieval_rejects_non_positive_top_k() -> None:
    with pytest.raises(ValueError, match="top_k"):
        await retrieve_documentation(
            "appointment status",
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            budget=ExecutionBudget(),
            top_k=0,
        )


@pytest.mark.asyncio
async def test_retrieval_uses_budget_bound_and_returns_full_chunks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeBridge:
        def __init__(self, url: str, *, auth_token: str | None = None) -> None:
            assert url == "http://rag.test/mcp"
            assert auth_token == "token"

        async def __aenter__(self) -> FakeBridge:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            calls.append((name, arguments))
            if name == "search_docs":
                return {
                    "query": "appointment status",
                    "results": [
                        {
                            "chunk_id": "chunk-1",
                            "title": "Appointments",
                            "heading_path": "Appointments > Status",
                            "score": -1.0,
                            "preview": "status docs",
                        }
                    ],
                    "index_version": "index-v1",
                }
            return {
                "chunk_id": "chunk-1",
                "doc_id": "appointments",
                "title": "Appointments",
                "heading_path": "Appointments > Status",
                "source_path": "appointments.html",
                "text": "Appointment status documentation.",
                "source": "rag_index",
            }

    monkeypatch.setattr("retrieval.client.MCPToolBridge", FakeBridge)
    settings = SimpleNamespace(rag_mcp_url="http://rag.test/mcp", mcp_auth_token="token")
    budget = ExecutionBudget(max_retrieved_chunks=1)
    result = await retrieve_documentation(
        "appointment status",
        settings,  # type: ignore[arg-type]
        TraceLogger(str(tmp_path)),  # type: ignore[arg-type]
        budget=budget,
        top_k=10,
    )

    assert result.index_version == "index-v1"
    assert result.chunks[0].document_id == "appointments"
    assert calls[0] == ("search_docs", {"query": "appointment status", "top_k": 1})
