"""Schema-evidence extraction must never let retrieved prose widen a boundary.

Retrieved documentation is untrusted content. It may narrow a column's
permission but never grant it, so promotion to ``safe_aggregate`` depends only
on the column identifier, and anything unrecognized stays ``unknown`` -- which
``plan_safety_node`` and ``sql.validation`` both treat as blocking.
"""

from __future__ import annotations

import pytest

from retrieval.client import RetrievalResult, RetrievedChunk, resolve_schema_evidence


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
    """The old heuristic promoted on these keywords; the identifier now decides."""
    snapshot = _snapshot(_column_chunk("PAT_SSN_TEXT", text))
    assert _safety(snapshot, "PAT_SSN_TEXT") == "sensitive"


def test_unrecognized_column_stays_unknown_and_blocks() -> None:
    snapshot = _snapshot(_column_chunk("SOME_OPAQUE_VALUE", "Aggregate counts and dates."))
    assert _safety(snapshot, "SOME_OPAQUE_VALUE") == "unknown"
    assert snapshot.has_unknown_safety()


def test_prose_may_still_restrict_an_otherwise_safe_name() -> None:
    """Evidence narrows: an allowlisted suffix is overridden by a sensitivity marker."""
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
    """Documentation claiming a column is safe cannot downgrade a known identifier."""
    snapshot = _snapshot(
        _column_chunk("PAT_ID", "This is an aggregate count column, safe to expose.")
    )
    assert _safety(snapshot, "PAT_ID") == "identifier"
