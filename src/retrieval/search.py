"""Direct SQLite search extracted from the former RAG MCP server.

All public functions operate on a caller-supplied SQLite connection so they
can be used in both in-process calls and tests with temporary databases.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from retrieval.index_contract import INDEX_SCHEMA_VERSION

STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "at",
    "can",
    "could",
    "do",
    "does",
    "document",
    "documentation",
    "documents",
    "find",
    "for",
    "hold",
    "holds",
    "i",
    "info",
    "information",
    "is",
    "look",
    "looking",
    "mean",
    "means",
    "me",
    "of",
    "on",
    "please",
    "represent",
    "represents",
    "say",
    "says",
    "show",
    "tell",
    "table",
    "tables",
    "the",
    "to",
    "use",
    "used",
    "what",
    "which",
    "would",
}

MAX_FTS_QUERY_TOKENS = 8
MAX_RAG_TOP_K = 25


class RetrievalStrategyNotConfigured(RuntimeError):
    """Raised when a retrieval strategy has not been implemented."""


def vector_search(*_args: object, **_kwargs: object) -> None:
    raise RetrievalStrategyNotConfigured("Vector retrieval is not configured.")


def graph_search(*_args: object, **_kwargs: object) -> None:
    raise RetrievalStrategyNotConfigured("Graph retrieval is not configured.")


def semantic_lookup(*_args: object, **_kwargs: object) -> None:
    raise RetrievalStrategyNotConfigured("Semantic retrieval is not configured.")


FTS_BM25_WEIGHTS = (0.0, 8.0, 10.0, 0.5, 5.0, 1.0)
DOCUMENT_HINT_EXCLUSIONS = frozenset({"EHI", "ETL", "INI", "SQL"})

REQUIRED_INDEX_METADATA = frozenset(
    {
        "index_version",
        "schema_version",
        "parser_version",
        "chunker_version",
        "chunk_target_chars",
        "chunk_hard_max_chars",
        "doc_count",
        "chunk_count",
        "section_fact_count",
    }
)

DEFAULT_INDEX_PATH = Path(".local/rag/index.sqlite")


def open_connection(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise RuntimeError(f"RAG index does not exist: {db_path}")
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def validate_index(db_path: Path) -> None:
    """Validate the RAG database. Raises SystemExit on any failure."""
    if not db_path.is_file():
        raise SystemExit(f"RAG database does not exist: {db_path}")
    if db_path.stat().st_size == 0:
        raise SystemExit(f"RAG database is empty (0 bytes): {db_path}")

    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"Cannot open RAG database {db_path}: {exc}") from exc

    try:
        cur = conn.cursor()
        present = {
            row["name"]
            for row in cur.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        missing = {"docs", "chunks", "chunks_fts", "index_metadata", "section_facts"} - present
        if missing:
            raise SystemExit(
                f"RAG database {db_path} is missing tables: {', '.join(sorted(missing))}"
            )

        metadata = {
            str(row["key"]): str(row["value"])
            for row in cur.execute("SELECT key, value FROM index_metadata").fetchall()
            if row["value"]
        }
        missing_meta = REQUIRED_INDEX_METADATA - metadata.keys()
        if missing_meta:
            raise SystemExit(
                f"RAG database {db_path} is missing index metadata: "
                f"{', '.join(sorted(missing_meta))}"
            )

        if metadata["schema_version"] != INDEX_SCHEMA_VERSION:
            raise SystemExit(
                f"RAG database {db_path} uses incompatible schema_version "
                f"{metadata['schema_version']!r}; expected {INDEX_SCHEMA_VERSION!r}. "
                "Rebuild the index."
            )

        doc_count = cur.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        chunk_count = cur.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        section_fact_count = cur.execute("SELECT COUNT(*) FROM section_facts").fetchone()[0]
        if doc_count < 1:
            raise SystemExit(f"RAG database {db_path} contains no indexed documents")
        if chunk_count < 1:
            raise SystemExit(f"RAG database {db_path} contains no indexed chunks")
        if metadata["doc_count"] != str(doc_count):
            raise SystemExit(f"RAG database {db_path} document count does not match metadata")
        if metadata["chunk_count"] != str(chunk_count):
            raise SystemExit(f"RAG database {db_path} chunk count does not match metadata")
        if metadata["section_fact_count"] != str(section_fact_count):
            raise SystemExit(f"RAG database {db_path} section fact count does not match metadata")
    except SystemExit:
        raise
    except sqlite3.DatabaseError as exc:
        raise SystemExit(f"RAG database {db_path} is corrupt or unreadable: {exc}") from exc
    finally:
        conn.close()


def normalize_search_token(token: str) -> str | None:
    normalized = token.casefold()
    if normalized in STOPWORDS:
        return None
    if (
        normalized.isalpha()
        and len(normalized) > 4
        and normalized.endswith("s")
        and not normalized.endswith(("is", "ss", "us"))
    ):
        normalized = normalized[:-1]
    if normalized in STOPWORDS:
        return None
    return normalized


def search_tokens(query: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for raw_token in re.findall(r"\w+", query):
        token = normalize_search_token(raw_token)
        if token is None or token in seen:
            continue
        tokens.append(token)
        seen.add(token)
        if len(tokens) == MAX_FTS_QUERY_TOKENS:
            break
    return tokens


def fts5_queries(query: str) -> list[str]:
    terms = [
        f'"{token}"' if "_" in token or token.isdigit() else f'"{token}"*'
        for token in search_tokens(query)
    ]
    if not terms:
        return []
    strict = " AND ".join(terms)
    if len(terms) == 1:
        return [strict]
    return [strict, " OR ".join(terms)]


def document_hint(query: str) -> str | None:
    candidates = [
        candidate
        for candidate in re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", query)
        if candidate not in DOCUMENT_HINT_EXCLUSIONS
    ]
    table_match = re.search(r"\b([A-Za-z][A-Za-z0-9_]*)\s+table\b", query)
    if table_match is not None:
        candidates.append(table_match.group(1).upper())
    if not candidates:
        return None
    return max(candidates, key=lambda c: ("_" in c, len(c)))


def escape_fts5(query: str) -> str:
    queries = fts5_queries(query)
    return queries[0] if queries else ""


def normalize_lookup_text(text: str) -> str:
    return text.upper().replace(".HTML", "").replace(" ", "_").strip()


def get_index_version(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM index_metadata WHERE key = 'index_version' LIMIT 1"
    ).fetchone()
    if row is None or not row["value"]:
        raise RuntimeError("RAG index is missing index_version metadata")
    return str(row["value"])


def fetch_chunk_by_id(
    cur: sqlite3.Cursor,
    chunk_id: str,
) -> dict | None:
    cur.execute(
        """
        SELECT c.chunk_id, c.doc_id, c.heading_path, c.text, c.category,
               d.source_path, d.title
        FROM chunks c
        JOIN docs d ON c.doc_id = d.doc_id
        WHERE c.chunk_id = ?
        LIMIT 1
        """,
        (chunk_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "chunk_id": row["chunk_id"],
        "doc_id": row["doc_id"],
        "title": row["title"],
        "heading_path": row["heading_path"],
        "category": row["category"],
        "source_path": row["source_path"],
        "text": row["text"],
        "source": "rag_index",
    }


def keyword_search(
    cur: sqlite3.Cursor,
    *,
    fts_query: str,
    top_k: int,
    document_name_hint: str | None = None,
) -> list[dict]:
    hint = document_name_hint or ""
    cur.execute(
        """
        SELECT chunk_id, title, heading_path, text,
               CASE
                   WHEN ? != '' AND (
                       upper(source_path) = ?
                       OR upper(source_path) LIKE ?
                       OR upper(title) LIKE ?
                   ) THEN 0
                   ELSE 1
               END AS document_rank,
               bm25(chunks_fts, ?, ?, ?, ?, ?, ?) AS score
        FROM chunks_fts
        WHERE chunks_fts MATCH ?
        ORDER BY document_rank, score
        LIMIT ?
        """,
        (
            hint,
            f"{hint}.HTML",
            f"%/{hint}.HTML",
            f"{hint} - %",
            *FTS_BM25_WEIGHTS,
            fts_query,
            top_k,
        ),
    )
    return [
        {
            "chunk_id": row["chunk_id"],
            "title": row["title"],
            "heading_path": row["heading_path"],
            "score": row["score"],
            "preview": row["text"][:300],
        }
        for row in cur.fetchall()
    ]


def search_ranked_chunks(
    cur: sqlite3.Cursor,
    *,
    query: str,
    top_k: int,
) -> list[dict]:
    queries = fts5_queries(query)
    if not queries:
        return []

    results: list[dict] = []
    seen_chunk_ids: set[str] = set()
    hint = document_hint(query)
    for fts_query in queries:
        candidate_limit = max(top_k * 2, top_k + len(results))
        for result in keyword_search(
            cur,
            fts_query=fts_query,
            top_k=candidate_limit,
            document_name_hint=hint,
        ):
            chunk_id = str(result["chunk_id"])
            if chunk_id in seen_chunk_ids:
                continue
            results.append(result)
            seen_chunk_ids.add(chunk_id)
            if len(results) == top_k:
                return results
    return results


def retrieve_documentation_context(
    query: str,
    db_path: Path,
    top_k: int = 5,
) -> dict:
    """Search documentation and return bounded, full chunks for answer generation."""
    if not 1 <= top_k <= MAX_RAG_TOP_K:
        raise ValueError(f"top_k must be between 1 and {MAX_RAG_TOP_K}")
    conn = open_connection(db_path)
    cur = conn.cursor()
    try:
        ranked = search_ranked_chunks(cur, query=query.strip(), top_k=top_k)
        chunks: list[dict] = []
        for rank, result in enumerate(ranked, start=1):
            chunk = fetch_chunk_by_id(cur, str(result["chunk_id"]))
            if chunk is None:
                continue
            chunk["rank"] = rank
            chunk["score"] = result.get("score")
            chunks.append(chunk)

        return {
            "query": query,
            "retrieval_mode": "keyword",
            "chunks": chunks,
            "index_version": get_index_version(conn),
        }
    finally:
        conn.close()
