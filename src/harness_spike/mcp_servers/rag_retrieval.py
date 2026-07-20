from __future__ import annotations

import sqlite3
from pathlib import Path

from fastmcp import FastMCP
from pydantic import BaseModel, Field


mcp = FastMCP("rag_retrieval")

RAG_DB_PATH = Path(__file__).resolve().parents[3] / "rag" / "one_file_rag.sqlite"


class SearchDocsArgs(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class GetDocChunkArgs(BaseModel):
    chunk_id: str = Field(min_length=1)


def get_rag_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(RAG_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def escape_fts5(query: str) -> str:
    special_chars = '()+-*:"'
    for char in special_chars:
        query = query.replace(char, " ")
    return " ".join(query.split())


# Retrieval tool
@mcp.tool
def search_docs(query: str, top_k: int = 5) -> dict[str, object]:
    """Search indexed HTML documentation chunks."""
    args = SearchDocsArgs(query=query.strip(), top_k=top_k)
    conn = get_rag_connection()
    cur = conn.cursor()

    try:
        fts_query = escape_fts5(args.query)
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
            "source": "rag_index",
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
    mcp.run(transport="http", host="localhost", port=8005, path="/mcp")


if __name__ == "__main__":
    main()
