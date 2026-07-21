from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from mcp_servers.auth import build_service_auth
from retrieval.index_contract import INDEX_SCHEMA_VERSION

mcp = FastMCP("rag_retrieval", auth=build_service_auth("rag"))

DEFAULT_RAG_DB_RELATIVE_PATH = Path("var") / "rag" / "index.sqlite"

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
FTS_BM25_WEIGHTS = (0.0, 8.0, 10.0, 0.5, 5.0, 1.0)
DOCUMENT_HINT_EXCLUSIONS = frozenset({"EHI", "ETL", "INI", "SQL"})


class SearchDocsArgs(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(ge=1)


class GetDocChunkArgs(BaseModel):
    chunk_id: str = Field(min_length=1)

class RetrieveDocumentationContextArgs(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1)

class FindTableDocArgs(BaseModel):
    table_name: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1)

class GetDocSectionArgs(BaseModel):
    doc_query: str = Field(min_length=1)
    section_query: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1)

class SearchColumnsArgs(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1)


def _db_path() -> Path:
    configured_path = os.getenv("RAG_DB_PATH")
    if configured_path:
        return Path(configured_path).expanduser().resolve()

    cwd = Path.cwd().resolve()
    for root in (cwd, *cwd.parents):
        candidate = root / DEFAULT_RAG_DB_RELATIVE_PATH
        if candidate.is_file():
            return candidate.resolve()

    return (cwd / DEFAULT_RAG_DB_RELATIVE_PATH).resolve()


def validate_rag_db(db_path: Path) -> None:
    """Validate the RAG database at startup. Raises SystemExit on any failure."""
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
            for row in cur.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
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
        missing_metadata = REQUIRED_INDEX_METADATA - metadata.keys()
        if missing_metadata:
            raise SystemExit(
                f"RAG database {db_path} is missing index metadata: "
                f"{', '.join(sorted(missing_metadata))}"
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
            raise SystemExit(
                f"RAG database {db_path} section fact count does not match metadata"
            )

        orphaned_chunk = cur.execute(
            """
            SELECT c.chunk_id
            FROM chunks AS c
            LEFT JOIN docs AS d ON c.doc_id = d.doc_id
            WHERE d.doc_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if orphaned_chunk is not None:
            raise SystemExit(
                f"RAG database {db_path} has a chunk with no matching document"
                f" (chunk_id={orphaned_chunk['chunk_id']!r})"
            )

        orphaned_fact = cur.execute(
            """
            SELECT f.doc_id, f.heading_path
            FROM section_facts AS f
            LEFT JOIN docs AS d ON f.doc_id = d.doc_id
            WHERE d.doc_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if orphaned_fact is not None:
            raise SystemExit(
                f"RAG database {db_path} has a section fact with no matching document"
                f" (doc_id={orphaned_fact['doc_id']!r}, "
                f"heading_path={orphaned_fact['heading_path']!r})"
            )

        orphan = cur.execute(
            """
            SELECT f.chunk_id
            FROM chunks_fts AS f
            LEFT JOIN chunks AS c ON f.chunk_id = c.chunk_id
            WHERE c.chunk_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if orphan is not None:
            raise SystemExit(
                f"RAG database {db_path} has an FTS entry with no matching chunk"
                f" (chunk_id={orphan['chunk_id']!r})"
            )

        fts_count = cur.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        if fts_count < chunk_count:
            raise SystemExit(
                f"RAG database {db_path} has a chunk with no matching FTS entry "
                f"({fts_count} FTS entries for {chunk_count} chunks)"
            )
        if fts_count > chunk_count:
            raise SystemExit(
                f"RAG database {db_path} has {fts_count} FTS entries for "
                f"{chunk_count} chunks"
            )

    except SystemExit:
        raise
    except sqlite3.DatabaseError as exc:
        raise SystemExit(f"RAG database {db_path} is corrupt or unreadable: {exc}") from exc
    finally:
        conn.close()


def get_rag_connection() -> sqlite3.Connection:
    db_path = _db_path()
    if not db_path.is_file():
        raise RuntimeError(f"RAG index does not exist: {db_path}")
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_search_token(token: str) -> str | None:
    """Normalize one user-query token without stemming clinical identifiers."""
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
    """Return bounded, de-duplicated content tokens for FTS retrieval."""
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
    """Build a precise FTS query followed by a broader fallback query."""
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
    """Extract an explicit table-like identifier for exact document promotion."""
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
    return max(candidates, key=lambda candidate: ("_" in candidate, len(candidate)))


def escape_fts5(query: str) -> str:
    """Return the strict normalized FTS5 query for compatibility callers."""
    queries = fts5_queries(query)
    return queries[0] if queries else ""

#DOC_NAME = doc name = DOC_NAME.html
def normalize_lookup_text(text: str) -> str:
    return text.upper().replace(".HTML", "").replace(" ", "_").strip()


def get_index_version(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM index_metadata WHERE key = 'index_version' LIMIT 1"
    ).fetchone()
    if row is None or not row["value"]:
        raise RuntimeError("RAG index is missing index_version metadata")
    return str(row["value"])


#Helpers
def fetch_chunk_by_id(
    cur: sqlite3.Cursor,
    chunk_id: str,
) -> dict[str, object] | None:
    cur.execute(
        """
        SELECT
            c.chunk_id,
            c.doc_id,
            c.heading_path,
            c.text,
            d.source_path,
            d.title
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
) -> list[dict[str, object]]:
    hint = document_name_hint or ""
    cur.execute(
        """
        SELECT
            chunk_id,
            title,
            heading_path,
            text,
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
) -> list[dict[str, object]]:
    queries = fts5_queries(query)
    if not queries:
        return []

    results: list[dict[str, object]] = []
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



# Retrieval tool
@mcp.tool
def search_docs(query: str, top_k: int) -> dict[str, object]:
    """Search indexed HTML documentation chunks."""
    args = SearchDocsArgs(query=query.strip(), top_k=top_k)
    conn = get_rag_connection()
    cur = conn.cursor()
    try:
        results = search_ranked_chunks(cur, query=args.query, top_k=args.top_k)

        return {
            "query": args.query,
            "retrieval_mode": "keyword",
            "results": results,
            "index_version": get_index_version(conn),
        }
    finally:
        conn.close()


# Fetch one chunk
@mcp.tool
def get_doc_chunk(chunk_id: str) -> dict[str, object]:
    """Fetch the full text and metadata for one retrieved documentation chunk."""
    args = GetDocChunkArgs(chunk_id=chunk_id.strip())
    conn = get_rag_connection()
    cur = conn.cursor()
    chunk = fetch_chunk_by_id(cur, args.chunk_id)
    try:
        if chunk is None:
            return {
                "error": "chunk_not_found",
                "chunk_id": args.chunk_id,
                "source": "rag_index",
            }
        return chunk
    finally:
        conn.close()


#One call to search and fetch chunks
@mcp.tool
def retrieve_documentation_context(query: str, top_k: int = 5) -> dict[str, object]:
    """Search documentation and return full bounded chunks for answer generation."""
    args = RetrieveDocumentationContextArgs(query=query.strip(), top_k=top_k)
    conn = get_rag_connection()
    cur = conn.cursor()

    try:
        ranked = search_ranked_chunks(cur, query=args.query, top_k=args.top_k)
        chunks: list[dict[str, object]] = []

        for rank, result in enumerate(ranked, start=1):
            chunk = fetch_chunk_by_id(cur, str(result["chunk_id"]))
            if chunk is None:
                continue
            chunk["rank"] = rank
            chunk["score"] = result.get("score")
            chunks.append(chunk)

        return {
            "query": args.query,
            "retrieval_mode": "keyword",
            "chunks": chunks,
            "index_version": get_index_version(conn),
        }
    finally:
        conn.close()

@mcp.tool
def find_table_doc(table_name: str, top_k: int = 5) -> dict[str, object]:
    """Find documentation pages by Clarity table/document name."""
    args = FindTableDocArgs(table_name=table_name.strip(), top_k=top_k)
    normalized = normalize_lookup_text(args.table_name)
    pattern = f"%{normalized}%"

    conn = get_rag_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT
                d.doc_id,
                d.source_path,
                d.title,
                COUNT(c.chunk_id) AS chunk_count
            FROM docs d
            LEFT JOIN chunks c ON d.doc_id = c.doc_id
            WHERE
                upper(d.source_path) LIKE ?
                OR upper(d.title) LIKE ?
            GROUP BY d.doc_id, d.source_path, d.title
            ORDER BY
                CASE
                    WHEN upper(d.source_path) = ? THEN 0
                    WHEN upper(d.source_path) LIKE ? THEN 1
                    ELSE 2
                END,
                d.source_path
            LIMIT ?
            """,
            (
                pattern,
                pattern,
                f"{normalized}.HTML",
                f"%{normalized}.HTML",
                args.top_k,
            ),
        )

        return {
            "query": args.table_name,
            "matches": [
                {
                    "doc_id": row["doc_id"],
                    "source_path": row["source_path"],
                    "title": row["title"],
                    "chunk_count": row["chunk_count"],
                }
                for row in cur.fetchall()
            ],
            "index_version": get_index_version(conn),
        }
    finally:
        conn.close()


@mcp.tool
def get_doc_section(
    doc_query: str,
    section_query: str,
    top_k: int = 10,
) -> dict[str, object]:
    """Fetch chunks from a specific documentation page section."""
    args = GetDocSectionArgs(
        doc_query=doc_query.strip(),
        section_query=section_query.strip(),
        top_k=top_k,
    )

    doc_pattern = f"%{normalize_lookup_text(args.doc_query)}%"
    section_pattern = f"%{args.section_query.upper()}%"

    conn = get_rag_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT
                c.chunk_id,
                c.doc_id,
                c.category,
                c.heading_path,
                c.text,
                d.source_path,
                d.title
            FROM chunks c
            JOIN docs d ON c.doc_id = d.doc_id
            WHERE
                (
                    upper(d.source_path) LIKE ?
                    OR upper(d.title) LIKE ?
                )
                AND (
                    upper(c.heading_path) LIKE ?
                    OR upper(c.category) LIKE ?
                )
            ORDER BY c.chunk_index
            LIMIT ?
            """,
            (
                doc_pattern,
                doc_pattern,
                section_pattern,
                section_pattern,
                args.top_k,
            ),
        )

        return {
            "doc_query": args.doc_query,
            "section_query": args.section_query,
            "chunks": [
                {
                    "chunk_id": row["chunk_id"],
                    "doc_id": row["doc_id"],
                    "source_path": row["source_path"],
                    "title": row["title"],
                    "category": row["category"],
                    "heading_path": row["heading_path"],
                    "text": row["text"],
                }
                for row in cur.fetchall()
            ],
            "index_version": get_index_version(conn),
        }
    finally:
        conn.close()


@mcp.tool
def search_columns(query: str, top_k: int = 10) -> dict[str, object]:
    """Search column-information documentation chunks."""
    args = SearchColumnsArgs(query=query.strip(), top_k=top_k)
    fts_query = escape_fts5(args.query)

    conn = get_rag_connection()
    cur = conn.cursor()

    try:
        if not fts_query:
            return {
                "query": args.query,
                "matches": [],
                "index_version": get_index_version(conn),
            }

        cur.execute(
            """
            SELECT
                chunk_id,
                title,
                heading_path,
                text,
                bm25(chunks_fts) AS score
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
              AND (
                  category = 'column_info'
                  OR heading_path LIKE '%Column-Information%'
              )
            ORDER BY score
            LIMIT ?
            """,
            (fts_query, args.top_k),
        )

        return {
            "query": args.query,
            "matches": [
                {
                    "chunk_id": row["chunk_id"],
                    "title": row["title"],
                    "heading_path": row["heading_path"],
                    "score": row["score"],
                    "preview": row["text"][:500],
                }
                for row in cur.fetchall()
            ],
            "index_version": get_index_version(conn),
        }
    finally:
        conn.close()



def main() -> None:
    validate_rag_db(_db_path())
    mcp.run(transport="http", host="localhost", port=8005, path="/mcp")


if __name__ == "__main__":
    main()
