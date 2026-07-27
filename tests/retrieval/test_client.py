"""Tests for the retrieval client (direct SQLite, no MCP)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_host.budget import ExecutionBudget
from retrieval.client import RetrievedChunk, retrieve_documentation
from sql.models import SchemaColumn, SchemaTable


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
                    source_evidence="appointments-status",
                )
            ],
            source_chunk_ids=[],  # must have at least 1
        )


def test_retrieval_rejects_non_positive_top_k(tmp_path: Path) -> None:
    budget = ExecutionBudget()
    with pytest.raises(ValueError, match="top_k"):
        retrieve_documentation(
            "appointment status",
            tmp_path / "nonexistent.sqlite",
            budget=budget,
            top_k=0,
        )


def test_retrieval_uses_budget_bound(monkeypatch, tmp_path: Path) -> None:
    """retrieve_documentation must respect ExecutionBudget.max_retrieved_chunks."""
    from retrieval import search

    calls: list[dict] = []

    def fake_retrieve(query, db_path, top_k=5):
        calls.append({"query": query, "top_k": top_k})
        return {
            "query": query,
            "retrieval_mode": "keyword",
            "chunks": [
                {
                    "chunk_id": "chunk-1",
                    "doc_id": "appointments",
                    "title": "Appointments",
                    "heading_path": "Status",
                    "category": "column_info",
                    "source_path": "appointments.html",
                    "text": "Appointment status documentation.",
                    "rank": 1,
                    "score": -1.0,
                    "source": "rag_index",
                }
            ],
            "index_version": "index-v1",
        }

    monkeypatch.setattr(search, "retrieve_documentation_context", fake_retrieve)

    budget = ExecutionBudget(max_retrieved_chunks=1)
    result = retrieve_documentation(
        "appointment status",
        tmp_path / "test.sqlite",
        budget=budget,
        top_k=10,
    )

    assert result.index_version == "index-v1"
    assert result.chunks[0].document_id == "appointments"
    assert calls == [{"query": "appointment status", "top_k": 1}]
