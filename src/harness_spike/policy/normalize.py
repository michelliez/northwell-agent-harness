from __future__ import annotations

import re
import unicodedata

ZERO_WIDTH_CHARS = {
    "\u200b",
    "\u200c",
    "\u200d",
    "\ufeff",
    "\u00ad",
    "\u00a0",
}

LEETSPEAK_TRANSLATION = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
    }
)


def normalize_prompt(text: str) -> str:
    q = unicodedata.normalize("NFKC", text)
    q = q.lower()
    for char in ZERO_WIDTH_CHARS:
        q = q.replace(char, " ")
    q = re.sub(r"[^a-z0-9]+", " ", q)
    return " ".join(q.split())


def compact_prompt(text: str) -> str:
    q = unicodedata.normalize("NFKC", text)
    q = q.lower()
    for char in ZERO_WIDTH_CHARS:
        q = q.replace(char, "")
    return re.sub(r"[^a-z0-9]", "", q)


def normalize_leetspeak(text: str) -> str:
    return text.translate(LEETSPEAK_TRANSLATION)


def contains_phrase(q: str, term: str) -> bool:
    return f" {term} " in f" {q} "


SCHEMA_METADATA_TERMS = (
    "schema",
    "column",
    "columns",
    "field",
    "fields",
    "primary key",
    "table definition",
    "metadata",
    "describe",
)

ROW_LEVEL_OUTPUT_TERMS = (
    "row",
    "rows",
    "record",
    "records",
    "patient name",
    "patient names",
    "mrn",
    "patient id",
    "address",
    "phone",
    "email",
    "raw",
    "export",
    "download",
)


def is_schema_metadata_request(q: str) -> bool:
    """Return whether the request asks for metadata rather than patient rows."""
    asks_about_schema = any(contains_phrase(q, term) for term in SCHEMA_METADATA_TERMS)
    asks_for_rows = any(contains_phrase(q, term) for term in ROW_LEVEL_OUTPUT_TERMS)
    return asks_about_schema and not asks_for_rows


def matches_blocked_term(text: str, q: str, term: str) -> bool:
    if contains_phrase(q, term):
        return True

    leetspeak_q = normalize_prompt(normalize_leetspeak(text))
    if contains_phrase(leetspeak_q, term):
        return True

    return contains_obfuscated_term(text, term)


def contains_obfuscated_term(text: str, term: str) -> bool:
    """Catch punctuation or spacing inserted inside blocked terms."""
    compact_term = compact_prompt(term)
    if not compact_term:
        return False

    normalized_text = unicodedata.normalize("NFKC", text).lower()
    for char in ZERO_WIDTH_CHARS:
        normalized_text = normalized_text.replace(char, " ")

    pattern = (
        r"(?<![a-z0-9])"
        + r"[^a-z0-9]*".join(re.escape(char) for char in compact_term)
        + r"(?![a-z0-9])"
    )
    return re.search(pattern, normalized_text) is not None
