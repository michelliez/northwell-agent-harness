from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from mcp_servers.auth import build_service_auth

mcp = FastMCP("rag_retrieval", auth=build_service_auth("rag"))

DEFAULT_RAG_DB_PATH = Path(__file__).resolve().parents[3] / "var" / "rag" / "index.sqlite"

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


def get_rag_connection() -> sqlite3.Connection:
    db_path = Path(os.getenv("RAG_DB_PATH") or DEFAULT_RAG_DB_PATH).resolve()
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
) -> list[dict[str, object]]:
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
        (fts_query, top_k),
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
    fts_query = escape_fts5(query)
    if not fts_query:
        return []

    return keyword_search(
        cur,
        fts_query=fts_query,
        top_k=top_k,
    )



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
            "retrieval_mode": "hybrid",
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
    mcp.run(transport="http", host="localhost", port=8005, path="/mcp")


if __name__ == "__main__":
    main()
