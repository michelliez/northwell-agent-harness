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

DEFAULT_RAG_DB_PATH = Path(__file__).resolve().parents[3] / "var" / "rag" / "index.sqlite"

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
    "can",
    "does",
    "documentation",
    "find",
    "for",
    "is",
    "me",
    "of",
    "on",
    "say",
    "says",
    "show",
    "tell",
    "the",
    "to",
    "what",
}


class SearchDocsArgs(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(ge=1)


class GetDocChunkArgs(BaseModel):
    chunk_id: str = Field(min_length=1)


def _db_path() -> Path:
    return Path(os.getenv("RAG_DB_PATH") or DEFAULT_RAG_DB_PATH).resolve()


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

        missing_fts = cur.execute(
            """
            SELECT c.chunk_id
            FROM chunks AS c
            LEFT JOIN chunks_fts AS f ON c.chunk_id = f.chunk_id
            WHERE f.chunk_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if missing_fts is not None:
            raise SystemExit(
                f"RAG database {db_path} has a chunk with no matching FTS entry"
                f" (chunk_id={missing_fts['chunk_id']!r})"
            )

        fts_count = cur.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        if fts_count != chunk_count:
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


def escape_fts5(query: str) -> str:
    """Convert user text to a forgiving literal-token FTS5 query."""
    tokens = [
        token.lower()
        for token in re.findall(r"\w+", query)
        if token.lower() not in STOPWORDS
    ]
    if not tokens:
        tokens = [token.lower() for token in re.findall(r"\w+", query)]
    return " OR ".join(f'"{token}"*' for token in tokens)


def get_index_version(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM index_metadata WHERE key = 'index_version' LIMIT 1"
    ).fetchone()
    if row is None or not row["value"]:
        raise RuntimeError("RAG index is missing index_version metadata")
    return str(row["value"])


# Retrieval tool
@mcp.tool
def search_docs(query: str, top_k: int) -> dict[str, object]:
    """Search indexed HTML documentation chunks."""
    args = SearchDocsArgs(query=query.strip(), top_k=top_k)
    conn = get_rag_connection()
    cur = conn.cursor()

    try:
        fts_query = escape_fts5(args.query)
        if not fts_query:
            return {
                "query": args.query,
                "results": [],
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
            ORDER BY score
            LIMIT ?
            """,
            (fts_query, args.top_k),
        )
        rows = cur.fetchall()

        return {
            "query": args.query,
            "results": [
                {
                    "chunk_id": row["chunk_id"],
                    "title": row["title"],
                    "heading_path": row["heading_path"],
                    "score": row["score"],
                    "preview": row["text"][:300],
                }
                for row in rows
            ],
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

    try:
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
            (args.chunk_id,),
        )
        row = cur.fetchone()

        if row is None:
            return {
                "error": "chunk_not_found",
                "chunk_id": args.chunk_id,
                "source": "rag_index",
            }

        return {
            "chunk_id": row["chunk_id"],
            "doc_id": row["doc_id"],
            "title": row["title"],
            "heading_path": row["heading_path"],
            "source_path": row["source_path"],
            "text": row["text"],
            "source": "rag_index",
        }
    finally:
        conn.close()


def main() -> None:
    validate_rag_db(_db_path())
    mcp.run(transport="http", host="localhost", port=8005, path="/mcp")


if __name__ == "__main__":
    main()
