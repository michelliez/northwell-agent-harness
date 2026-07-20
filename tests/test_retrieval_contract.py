from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness_spike.agent_host.budget import ExecutionBudget
from harness_spike.agent_host.retrieval import (
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
async def test_retrieval_rejects_unbounded_top_k() -> None:
    with pytest.raises(ValueError, match="top_k"):
        await retrieve_documentation(
            "appointment status",
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            budget=ExecutionBudget(),
            top_k=21,
        )
