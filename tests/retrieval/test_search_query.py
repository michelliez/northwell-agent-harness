from __future__ import annotations

import sqlite3

from retrieval.search import (
    document_hint,
    fts5_queries,
    search_ranked_chunks,
    search_tokens,
)


def test_conversational_admissions_query_keeps_only_content_term() -> None:
    query = "which documents can I look at for admissions info"

    assert search_tokens(query) == ["admission"]
    assert fts5_queries(query) == ['"admission"*']


def test_search_tokens_preserve_clinical_identifiers_and_meaningful_suffixes() -> None:
    assert search_tokens("What does PAT_ENC status diagnosis mean?") == [
        "pat_enc",
        "status",
        "diagnosis",
    ]


def test_multiple_terms_use_strict_query_then_or_fallback() -> None:
    assert fts5_queries("appointment status code") == [
        '"appointment"* AND "status"* AND "code"*',
        '"appointment"* OR "status"* OR "code"*',
    ]


def test_identifiers_use_exact_terms_instead_of_prefix_matching() -> None:
    assert fts5_queries("What does PAT_ID represent?") == ['"pat_id"']
    assert fts5_queries("What does code 7020 mean?")[0] == '"code"* AND "7020"'


def test_document_hint_prefers_explicit_table_identifier() -> None:
    assert document_hint("What Chronicles INI does PAT_ENC use?") == "PAT_ENC"
    assert document_hint("What load type does the PATIENT table use?") == "PATIENT"


def test_ranked_search_filters_conversational_noise_and_weights_title() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE VIRTUAL TABLE chunks_fts USING fts5(
               chunk_id, source_path, title, category, heading_path, text
           )"""
    )
    conn.executemany(
        """INSERT INTO chunks_fts
           (chunk_id, source_path, title, category, heading_path, text)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (
                "admission",
                "ADMISSION_GUIDE.html",
                "ADMISSION_GUIDE",
                "metadata",
                "ADMISSION_GUIDE > Description",
                "Hospital encounter guidance.",
            ),
            (
                "noise",
                "NOISE.html",
                "NOISE",
                "metadata",
                "NOISE > Description",
                "Which documents can I look at for general information?",
            ),
        ],
    )

    results = search_ranked_chunks(
        conn.cursor(),
        query="which documents can I look at for admissions info",
        top_k=2,
    )

    assert [result["chunk_id"] for result in results] == ["admission"]


def test_ranked_search_falls_back_when_no_chunk_contains_every_term() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE VIRTUAL TABLE chunks_fts USING fts5(
               chunk_id, source_path, title, category, heading_path, text
           )"""
    )
    conn.executemany(
        """INSERT INTO chunks_fts
           (chunk_id, source_path, title, category, heading_path, text)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            ("appointment", "A.html", "A", "general", "A", "appointment"),
            ("status", "B.html", "B", "general", "B", "status"),
        ],
    )

    results = search_ranked_chunks(
        conn.cursor(),
        query="appointment status",
        top_k=2,
    )

    assert {result["chunk_id"] for result in results} == {"appointment", "status"}


def test_ranked_search_promotes_exact_document_hint_over_extension_table() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE VIRTUAL TABLE chunks_fts USING fts5(
               chunk_id, source_path, title, category, heading_path, text
           )"""
    )
    conn.executemany(
        """INSERT INTO chunks_fts
           (chunk_id, source_path, title, category, heading_path, text)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (
                "extension",
                "PAT_ENC_FORMS_USED.html",
                "PAT_ENC_FORMS_USED - Clarity Dictionary",
                "metadata",
                "PAT_ENC_FORMS_USED",
                "Chronicles INI PAT_ENC use use use",
            ),
            (
                "exact",
                "PAT_ENC.html",
                "PAT_ENC - Clarity Dictionary",
                "metadata",
                "PAT_ENC",
                "Chronicles INI use",
            ),
        ],
    )

    results = search_ranked_chunks(
        conn.cursor(),
        query="What Chronicles INI does PAT_ENC use?",
        top_k=2,
    )

    assert results[0]["chunk_id"] == "exact"
