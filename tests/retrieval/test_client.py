"""Tests for the retrieval client (direct SQLite, no MCP)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_host.budget import ExecutionBudget
from retrieval.client import (
    RetrievalResult,
    RetrievedChunk,
    resolve_schema_evidence,
    retrieve_documentation,
)
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

    def fake_retrieve(query, db_path, top_k=5, dense_index_dir=None):
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


def _column_chunk(column: str, text: str, *, table: str = "CLARITY_ADT") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"chunk-{column.lower()}",
        document_id=f"doc-{table.lower()}",
        source_path=f"{table}.html",
        heading_path=f"Column Information > {column}",
        category="column_info",
        text=text,
        rank=1,
    )


def _snapshot(*chunks: RetrievedChunk):
    return resolve_schema_evidence(
        RetrievalResult(query="q", chunks=list(chunks), index_version="test-index")
    )


def _safety(snapshot, column: str) -> str:
    for table in snapshot.tables:
        for col in table.columns:
            if col.name == column:
                return col.safety
    raise AssertionError(f"{column} missing from snapshot")


@pytest.mark.parametrize(
    "text",
    [
        "Aggregate count of visits by date.",
        "The status flag type for this record.",
        "Safe to count. Contains a date.",
    ],
)
def test_prose_alone_cannot_promote_a_column_to_safe(text: str) -> None:
    snapshot = _snapshot(_column_chunk("PAT_SSN_TEXT", text))
    assert _safety(snapshot, "PAT_SSN_TEXT") == "sensitive"


def test_unrecognized_column_stays_unknown_and_blocks() -> None:
    snapshot = _snapshot(_column_chunk("SOME_OPAQUE_VALUE", "Aggregate counts and dates."))
    assert _safety(snapshot, "SOME_OPAQUE_VALUE") == "unknown"
    assert snapshot.has_unknown_safety()


def test_prose_may_still_restrict_an_otherwise_safe_name() -> None:
    snapshot = _snapshot(
        _column_chunk("ADMIT_DATE", "This field is sensitive and must not be exposed.")
    )
    assert _safety(snapshot, "ADMIT_DATE") == "sensitive"


def test_allowlisted_suffix_promotes_without_reading_prose() -> None:
    snapshot = _snapshot(_column_chunk("ADMIT_DATE", "Opaque description with no useful markers."))
    assert _safety(snapshot, "ADMIT_DATE") == "safe_aggregate"
    assert not snapshot.has_unknown_safety()


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        ("PAT_ID", "identifier"),
        ("PAT_MRN", "identifier"),
        ("PAT_NAME", "sensitive"),
        ("HOME_ADDRESS", "sensitive"),
        ("CONTACT_PHONE", "sensitive"),
        ("VISIT_COUNT", "safe_aggregate"),
        ("ENC_TYPE", "safe_aggregate"),
    ],
)
def test_identifier_and_sensitive_names_are_restricted_by_name(column: str, expected: str) -> None:
    snapshot = _snapshot(_column_chunk(column, "Neutral description."))
    assert _safety(snapshot, column) == expected


def test_identifier_name_wins_over_a_prose_safety_claim() -> None:
    snapshot = _snapshot(
        _column_chunk("PAT_ID", "This is an aggregate count column, safe to expose.")
    )
    assert _safety(snapshot, "PAT_ID") == "identifier"


# -- Epic-convention suffixes added by corpus survey --


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        ("ABN_FLUP_STATUS_C", "safe_aggregate"),
        ("IS_WINNING_VARIANT_YN", "safe_aggregate"),
        ("ADMSN_DT", "safe_aggregate"),
        ("ABN_UPDATE_INS_DTTM", "safe_aggregate"),
        ("SLOT_TIME", "safe_aggregate"),
        ("LAST_INS_PMT_AMT", "safe_aggregate"),
        ("DEV_CATH_CNT", "safe_aggregate"),
        ("CREATE_TM", "safe_aggregate"),
        ("COVERED_DAYS", "safe_aggregate"),
    ],
)
def test_epic_convention_suffixes_promote_to_safe(column: str, expected: str) -> None:
    snapshot = _snapshot(_column_chunk(column, "Neutral description."))
    assert _safety(snapshot, column) == expected


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        ("ACCOUNT_TYPE_C", "safe_aggregate"),
        ("ADDR_HX_STATE_C", "sensitive"),
        ("PROTOCOL_CSN_SRC_C", "safe_aggregate"),
        ("ASSOC_CSN_TYPE_C", "safe_aggregate"),
        ("BANK_ACCT_TYPE_C", "safe_aggregate"),
    ],
)
def test_identifier_sensitive_precedence_with_c_suffix(column: str, expected: str) -> None:
    """_C promotion must not override identifier/sensitive name patterns."""
    snapshot = _snapshot(_column_chunk(column, "Neutral description."))
    assert _safety(snapshot, column) == expected


def test_sensitive_name_wins_over_yn_suffix() -> None:
    snapshot = _snapshot(_column_chunk("SSN_YN", "Neutral description."))
    assert _safety(snapshot, "SSN_YN") == "sensitive"


def test_sensitive_name_wins_over_dt_suffix() -> None:
    snapshot = _snapshot(_column_chunk("ADDRESS_DT", "Neutral description."))
    assert _safety(snapshot, "ADDRESS_DT") == "sensitive"
