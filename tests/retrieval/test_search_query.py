from __future__ import annotations

import sqlite3

from retrieval.search import (
    MAX_FTS_QUERY_TOKENS,
    document_hint,
    fts5_query,
    match_count_lookup,
    search_ranked_chunks,
    search_tokens,
    select_query_tokens,
)


def test_conversational_admissions_query_keeps_only_content_term() -> None:
    query = "which documents can I look at for admissions info"

    assert search_tokens(query) == ["admission"]
    assert fts5_query(query) == '"admission"*'


def test_search_tokens_preserve_clinical_identifiers_and_meaningful_suffixes() -> None:
    assert search_tokens("What does PAT_ENC status diagnosis mean?") == [
        "pat_enc",
        "status",
        "diagnosis",
    ]


def test_multiple_terms_join_as_a_disjunction() -> None:
    # Requiring every term to co-occur in one chunk returned nothing on 50 of 50
    # benchmark queries, so there is no conjunctive pass to prefer.
    assert fts5_query("appointment status code") == '"appointment"* OR "status"* OR "code"*'


def test_identifiers_use_exact_terms_instead_of_prefix_matching() -> None:
    assert fts5_query("What does PAT_ID represent?") == '"pat_id"'
    assert fts5_query("What does code 7020 mean?") == '"code"* OR "7020"'
    assert fts5_query("What does R1 mean?") == '"r1"'


def test_query_with_no_surviving_tokens_matches_nothing() -> None:
    assert fts5_query("what is it") == ""


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


def test_short_domain_terms_survive_as_exact_terms() -> None:
    assert fts5_query("ED visits and IP admissions") == ('"ed" OR "visit"* OR "ip" OR "admission"*')
    assert fts5_query("OR cases") == '"or" OR "case"*'
    assert fts5_query("current snapshot or history") == '"current"* OR "snapshot"* OR "history"*'


def test_token_selection_keeps_the_rarest_terms_not_the_earliest() -> None:
    tokens = ["reviewing", "cleanup", "feed", "hyperspace"]
    frequencies = {"reviewing": 90_000, "cleanup": 40_000, "feed": 8_000, "hyperspace": 651}

    selected = select_query_tokens(tokens, limit=2, match_count=frequencies.__getitem__)

    assert selected == ["feed", "hyperspace"]


def test_token_selection_prioritizes_an_identifier_only_when_it_can_match() -> None:
    tokens = ["patient", "encounter", "pat_enc"]
    frequencies = {"patient": 60_000, "encounter": 12_000, "pat_enc": 3}

    selected = select_query_tokens(
        tokens,
        limit=1,
        match_count=frequencies.__getitem__,
    )

    assert selected == ["pat_enc"]


def test_token_selection_drops_terms_that_cannot_match() -> None:
    tokens = ["unknown", "encounter", "missing_identifier"]
    frequencies = {"unknown": 0, "encounter": 12_000, "missing_identifier": 0}

    assert select_query_tokens(tokens, match_count=frequencies.__getitem__) == ["encounter"]


def test_zero_count_terms_cannot_crowd_a_real_term_out_of_the_cap() -> None:
    unknown = [f"unknown{index}" for index in range(MAX_FTS_QUERY_TOKENS)]
    tokens = [*unknown, "encounter"]

    selected = select_query_tokens(
        tokens,
        match_count=lambda term: 12_000 if term == "encounter" else 0,
    )

    assert selected == ["encounter"]


def test_numeric_values_are_not_prioritized_as_schema_identifiers() -> None:
    tokens = ["patient", "448219"]
    frequencies = {"patient": 60_000, "448219": 0}

    assert select_query_tokens(tokens, limit=1, match_count=frequencies.__getitem__) == ["patient"]


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


def test_match_count_estimates_prefix_posting_volume() -> None:
    conn = _fts_connection(
        [
            ("one", "A.html", "A", "metadata", "A", "admissions admissions"),
            ("two", "B.html", "B", "metadata", "B", "admission"),
            ("three", "C.html", "C", "metadata", "C", "hyperspace"),
        ]
    )

    lookup = match_count_lookup(conn.cursor())
    assert lookup is not None

    # "admission"* reaches both the singular and plural rows. The estimate sums
    # both vocabulary posting lists rather than scoring the exact singular form.
    assert lookup("admission") == 2
    assert lookup("hyperspace") == 1
    assert lookup("absent") == 0


def test_match_count_uses_real_match_rows_for_identifier_phrases() -> None:
    conn = _fts_connection(
        [
            ("one", "PAT_ENC.html", "PAT_ENC", "metadata", "PAT_ENC", "patient encounter"),
            ("two", "OTHER.html", "OTHER", "metadata", "OTHER", "patient only"),
        ]
    )

    lookup = match_count_lookup(conn.cursor())
    assert lookup is not None

    assert lookup("pat_enc") == 1


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

    assert "a0h_delete" in search_tokens(query, match_count=match_count_lookup(conn.cursor()))

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


def test_ranked_search_returns_chunks_holding_any_term() -> None:
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
