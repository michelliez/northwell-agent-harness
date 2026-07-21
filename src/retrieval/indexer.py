from __future__ import annotations

import argparse
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import Tag

# ~800 tokens at 4 chars/token; rows are batched until this limit before a new chunk starts
CHUNK_TARGET_CHARS = 3200
# ~1200 tokens; a single indivisible row that exceeds this will still be emitted as-is
CHUNK_HARD_MAX_CHARS = 4800


@dataclass(frozen=True)
class IndexedChunk:
    category: str
    heading_path: str
    text: str


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    """Rough token estimate: 4 chars per token (English prose approximation)."""
    return max(1, len(text) // 4)


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
                continue  # section_empty — no retrieval value
            elif isinstance(value, Tag) and value.name == "table":
                chunks.extend(table_to_chunks(classify_table(value), heading, value))
            elif str(value).strip() == "None":
                continue  # section_empty — no retrieval value

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
    """Render a small table (e.g. KeyValue) as a single pipe-delimited string."""
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


def table_to_chunks(category: str, heading: str, table: Tag) -> list[IndexedChunk]:
    """Split a content table into one or more chunks.

    Column-header rows (T1Head cells) are identified and prepended to every
    child chunk so that each chunk is self-contained.  Data rows are batched
    until CHUNK_TARGET_CHARS before a new chunk starts.
    """
    header_rows: list[str] = []
    data_rows: list[str] = []

    for row in table.find_all("tr"):
        row_text: list[str] = []
        is_header = False
        for cell in row.find_all(["th", "td"]):
            text = cell.get_text(" ", strip=True)
            if not text:
                continue
            if "T1Head" in css_classes(cell):
                is_header = True
                row_text.append(f"{text}:")
            else:
                row_text.append(text)
        if row_text:
            joined = " ".join(row_text)
            if is_header:
                header_rows.append(joined)
            else:
                data_rows.append(joined)

    header_str = " | ".join(header_rows)

    if not data_rows:
        return [IndexedChunk(category=category, heading_path=heading, text=header_str)] if header_str else []

    chunks: list[IndexedChunk] = []
    batch: list[str] = []
    batch_chars = len(header_str)

    for row in data_rows:
        row_chars = len(row) + 3  # 3 for the " | " separator
        if batch and batch_chars + row_chars > CHUNK_TARGET_CHARS:
            parts = ([header_str] + batch) if header_str else batch
            chunks.append(IndexedChunk(category=category, heading_path=heading, text=" | ".join(parts)))
            batch = []
            batch_chars = len(header_str)
        batch.append(row)
        batch_chars += row_chars

    if batch:
        parts = ([header_str] + batch) if header_str else batch
        chunks.append(IndexedChunk(category=category, heading_path=heading, text=" | ".join(parts)))

    return chunks


def discover_html_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted(input_path.rglob("*.html"))
    raise FileNotFoundError(input_path)


def create_schema(conn: sqlite3.Connection) -> None:
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
            token_count INTEGER NOT NULL,
            text_hash TEXT NOT NULL,
            FOREIGN KEY (doc_id) REFERENCES docs(doc_id)
        );

        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            chunk_id, source_path, title, category, heading_path, text
        );
        """
    )


def index_one_document(
    conn: sqlite3.Connection,
    html_path: Path,
    *,
    corpus_root: Path,
) -> tuple[str, int]:
    html_bytes = html_path.read_bytes()
    html = html_bytes.decode("utf-8", errors="replace")
    source_hash = hashlib.sha256(html_bytes).hexdigest()
    title, chunks = extract_chunks(html, fallback_title=html_path.stem)

    try:
        source_path = str(html_path.relative_to(corpus_root))
    except ValueError:
        source_path = html_path.name

    # doc_id is path-only so it survives content changes across re-indexing runs
    document_id = sha256_text(source_path)

    conn.execute(
        "INSERT INTO docs (doc_id, source_path, title, source_hash) VALUES (?, ?, ?, ?)",
        (document_id, source_path, title, source_hash),
    )

    # occurrence counts how many prior chunks share the same (heading_path, category) pair.
    # Chunks produced by splitting one large table all share the same heading+category, so
    # occurrence=0,1,2,… distinguishes them.  A chunk retains its chunk_id across re-indexing
    # as long as its text, heading, category, and occurrence position are unchanged.
    occurrence_counter: dict[tuple[str, str], int] = {}
    for chunk_index, chunk in enumerate(chunks):
        text_hash = sha256_text(chunk.text)
        occ_key = (chunk.heading_path, chunk.category)
        occurrence = occurrence_counter.get(occ_key, 0)
        occurrence_counter[occ_key] = occurrence + 1
        chunk_id = sha256_text(
            f"{document_id}:{chunk.heading_path}:{chunk.category}:{occurrence}:{text_hash}"
        )

        conn.execute(
            """INSERT INTO chunks
               (chunk_id, doc_id, chunk_index, category, heading_path, text, token_count, text_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chunk_id,
                document_id,
                chunk_index,
                chunk.category,
                chunk.heading_path,
                chunk.text,
                estimate_tokens(chunk.text),
                text_hash,
            ),
        )

        conn.execute(
            """INSERT INTO chunks_fts
               (chunk_id, source_path, title, category, heading_path, text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                chunk_id,
                source_path,
                title,
                chunk.category,
                chunk.heading_path,
                chunk.text,
            ),
        )

    return source_hash, len(chunks)


def build_index(
    input_path: Path,
    db_path: Path,
    *,
    limit: int | None = None,
) -> tuple[str, int, int]:
    html_files = discover_html_files(input_path)

    if limit is not None:
        html_files = html_files[:limit]

    if not html_files:
        raise ValueError(f"No HTML files found under {input_path}")

    corpus_root = input_path if input_path.is_dir() else input_path.parent

    db_path.parent.mkdir(parents=True, exist_ok=True)
    source_hashes: list[str] = []
    total_chunks = 0

    with sqlite3.connect(db_path) as conn:
        create_schema(conn)

        for html_file in html_files:
            source_hash, chunk_count = index_one_document(
                conn,
                html_file,
                corpus_root=corpus_root,
            )
            source_hashes.append(source_hash)
            total_chunks += chunk_count

        index_version = sha256_text("".join(source_hashes))
        conn.execute(
            "INSERT INTO index_metadata (key, value) VALUES ('index_version', ?)",
            (index_version,),
        )

    return index_version, len(html_files), total_chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="Index one approved HTML documentation file.")
    parser.add_argument("input_path", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--db", type=Path, default=Path("var/rag/index.sqlite"))
    args = parser.parse_args()
    if args.input_path.is_file() and args.input_path.suffix.lower() not in {".html", ".htm"}:
        parser.error("input_path must be an HTML file or a directory")

    version, doc_count, chunk_count = build_index(
        args.input_path,
        args.db,
        limit=args.limit,
    )

    print(
        f"Indexed {doc_count} docs / {chunk_count} chunks into "
        f"{args.db} (version={version})"
    )


if __name__ == "__main__":
    main()
