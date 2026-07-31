from __future__ import annotations

import sqlite3

from retrieval.search import (
    MAX_FTS_QUERY_TOKENS,
    document_frequency_lookup,
    document_hint,
    fts5_queries,
    search_ranked_chunks,
    search_tokens,
    select_query_tokens,
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


def test_contraction_fragments_and_filler_are_too_short_to_query() -> None:
    # `I'm` splits on the apostrophe and leaves `m`, which becomes the prefix
    # term "m"* and reaches a large fraction of the index for no intent.
    tokens = search_tokens("I'm reviewing the cleanup feed as it is in A0H_DELETE")

    assert "m" not in tokens
    assert "it" not in tokens
    assert "in" not in tokens
    assert "a0h_delete" in tokens


def test_short_tokens_carrying_a_digit_survive_as_identifiers() -> None:
    assert search_tokens("what does R1 mean") == ["r1"]


def test_token_selection_keeps_the_rarest_terms_not_the_earliest() -> None:
    tokens = ["reviewing", "cleanup", "feed", "hyperspace"]
    frequencies = {"reviewing": 90_000, "cleanup": 40_000, "feed": 8_000, "hyperspace": 651}

    selected = select_query_tokens(tokens, limit=2, document_frequency=frequencies.__getitem__)

    assert selected == ["feed", "hyperspace"]


def test_token_selection_never_drops_an_identifier_for_a_common_word() -> None:
    tokens = ["patient", "encounter", "pat_enc"]

    selected = select_query_tokens(
        tokens,
        limit=1,
        # An identifier is kept without consulting frequency at all.
        document_frequency=lambda _: 0,
    )

    assert selected == ["pat_enc"]


def test_token_selection_falls_back_to_position_without_an_index() -> None:
    tokens = [f"term{index}" for index in range(MAX_FTS_QUERY_TOKENS + 3)]

    assert select_query_tokens(tokens) == tokens[:MAX_FTS_QUERY_TOKENS]


def _fts_connection(rows: list[tuple[str, ...]]) -> sqlite3.Connection:
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
        rows,
    )
    return conn


def test_document_frequency_counts_the_prefix_range_not_the_exact_term() -> None:
    conn = _fts_connection(
        [
            ("one", "A.html", "A", "metadata", "A", "admissions admissions"),
            ("two", "B.html", "B", "metadata", "B", "admission"),
            ("three", "C.html", "C", "metadata", "C", "hyperspace"),
        ]
    )

    lookup = document_frequency_lookup(conn.cursor())
    assert lookup is not None

    # "admission"* reaches both the singular and plural rows; the exact term
    # alone would report one and make a stripped plural look artificially rare.
    assert lookup("admission") == 2
    assert lookup("hyperspace") == 1
    assert lookup("absent") == 0


def test_ranked_search_keeps_the_identifier_when_the_query_overflows_the_cap() -> None:
    conn = _fts_connection(
        [
            (
                "target",
                "A0H_DELETE.html",
                "A0H_DELETE",
                "metadata",
                "A0H_DELETE",
                "hyperspace access records deleted during the specified range",
            ),
        ]
        + [
            (
                f"noise{index}",
                f"NOISE{index}.html",
                f"NOISE{index}",
                "metadata",
                f"NOISE{index}",
                "reviewing incremental cleanup feed records during specified range",
            )
            for index in range(6)
        ]
    )

    # The identifier is the last word of a query that overflows the token cap.
    query = (
        "reviewing the incremental cleanup feed records during a "
        "specified range what does A0H_DELETE contain"
    )

    assert "a0h_delete" in search_tokens(
        query, document_frequency=document_frequency_lookup(conn.cursor())
    )

    results = search_ranked_chunks(conn.cursor(), query=query, top_k=3)
    assert results[0]["chunk_id"] == "target"


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
