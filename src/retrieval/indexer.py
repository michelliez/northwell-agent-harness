from __future__ import annotations

import argparse
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import Tag


@dataclass(frozen=True)
class IndexedChunk:
    category: str
    heading_path: str
    text: str


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_chunks(html: str, *, fallback_title: str) -> tuple[str, list[IndexedChunk]]:
    """Extract Clarity-style documentation sections from one HTML file."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "template", "nav", "footer", "header"]):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else fallback_title
    chunks: list[IndexedChunk] = []
    for content_div in soup.find_all("div", id="oContent"):
        header = content_div.find_previous("div", class_="header")
        document_title = header.get_text(" ", strip=True) if header else title

        metadata = content_div.find("table", class_="KeyValue")
        if metadata is not None:
            text = extract_table_content(metadata)
            if text:
                chunks.append(IndexedChunk("metadata", document_title, text))

        for subheader in content_div.find_all("table", class_="SubHeader3"):
            section_cell = subheader.find("td", id=True)
            section = (
                str(section_cell.get("id", "unnamed")).strip("_")
                if section_cell is not None
                else "unnamed"
            )
            heading = f"{document_title} > {section}"
            value = subheader.find_next_sibling()
            if value is None:
                continue
            if isinstance(value, Tag) and value.name == "span" and "NA" in css_classes(value):
                chunks.append(IndexedChunk("section_empty", heading, "No data"))
            elif isinstance(value, Tag) and value.name == "table":
                text = extract_table_content(value)
                if text:
                    chunks.append(IndexedChunk(classify_table(value), heading, text))
            elif str(value).strip() == "None":
                chunks.append(IndexedChunk("section_empty", heading, "No data"))

    if not chunks:
        text = soup.get_text(" ", strip=True)
        if text:
            chunks.append(IndexedChunk("document", title, text))
    return title, chunks


def css_classes(tag: Tag) -> list[str]:
    value = tag.get("class")
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def classify_table(table: Tag) -> str:
    classes = css_classes(table)
    if "SubList" in classes:
        return "column_info"
    if "List" in classes:
        return "table_data"
    if "KeyValue" in classes:
        return "metadata"
    return "table_generic"


def extract_table_content(table: Tag) -> str:
    parts: list[str] = []
    for row in table.find_all("tr"):
        row_text: list[str] = []
        for cell in row.find_all(["th", "td"]):
            text = cell.get_text(" ", strip=True)
            if not text:
                continue
            row_text.append(f"{text}:" if "T1Head" in css_classes(cell) else text)
        if row_text:
            parts.append(" ".join(row_text))
    return " | ".join(parts)


def build_index(html_path: Path, db_path: Path) -> tuple[str, int]:
    """Build a deterministic SQLite FTS index from one approved HTML document."""
    html_bytes = html_path.read_bytes()
    html = html_bytes.decode("utf-8", errors="replace")
    source_hash = hashlib.sha256(html_bytes).hexdigest()
    title, chunks = extract_chunks(html, fallback_title=html_path.stem)
    document_id = sha256_text(f"{html_path.name}:{source_hash}")
    index_version = sha256_text(
        source_hash
        + "".join(f"{chunk.category}:{chunk.heading_path}:{chunk.text}" for chunk in chunks)
    )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS index_metadata;
            DROP TABLE IF EXISTS docs;
            DROP TABLE IF EXISTS chunks;
            DROP TABLE IF EXISTS chunks_fts;
            CREATE TABLE index_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE docs (
                doc_id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                title TEXT NOT NULL,
                source_hash TEXT NOT NULL
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                category TEXT NOT NULL,
                heading_path TEXT NOT NULL,
                text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                FOREIGN KEY (doc_id) REFERENCES docs(doc_id)
            );
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                chunk_id, title, category, heading_path, text
            );
            """
        )
        conn.execute(
            "INSERT INTO index_metadata (key, value) VALUES ('index_version', ?)",
            (index_version,),
        )
        conn.execute(
            "INSERT INTO docs (doc_id, source_path, title, source_hash) VALUES (?, ?, ?, ?)",
            (document_id, html_path.name, title, source_hash),
        )
        for chunk_index, chunk in enumerate(chunks):
            text_hash = sha256_text(chunk.text)
            chunk_id = sha256_text(
                f"{document_id}:{chunk_index}:{chunk.category}:{chunk.heading_path}:{text_hash}"
            )
            conn.execute(
                """INSERT INTO chunks
                   (chunk_id, doc_id, chunk_index, category, heading_path, text, text_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    chunk_id,
                    document_id,
                    chunk_index,
                    chunk.category,
                    chunk.heading_path,
                    chunk.text,
                    text_hash,
                ),
            )
            conn.execute(
                """INSERT INTO chunks_fts
                   (chunk_id, title, category, heading_path, text) VALUES (?, ?, ?, ?, ?)""",
                (chunk_id, title, chunk.category, chunk.heading_path, chunk.text),
            )
    return index_version, len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Index one approved HTML documentation file.")
    parser.add_argument("html_file", type=Path)
    parser.add_argument("--db", type=Path, default=Path("var/rag/index.sqlite"))
    args = parser.parse_args()
    if args.html_file.suffix.lower() not in {".html", ".htm"}:
        parser.error("html_file must have an .html or .htm extension")
    version, count = build_index(args.html_file, args.db)
    print(f"Indexed {count} chunks into {args.db} (version={version})")


if __name__ == "__main__":
    main()
