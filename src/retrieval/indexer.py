from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import Tag

from retrieval.column_parser import parse_column_records
from retrieval.html_utils import (
    css_classes,
    discover_html_files,
    normalized_source_path,
    owned_cell_text,
    owned_cells,
    owned_rows,
)
from retrieval.index_contract import INDEX_CHUNKER_VERSION, INDEX_SCHEMA_VERSION

# ~800 tokens at 4 chars/token; rows are batched until this limit before a new chunk starts
CHUNK_TARGET_CHARS = 3200
# ~1200 tokens; no indexed chunk may exceed this value
CHUNK_HARD_MAX_CHARS = 4800

PARSER_VERSION = "clarity-html-v5"
CHUNKER_VERSION = INDEX_CHUNKER_VERSION
PROGRESS_INTERVAL = 400


@dataclass(frozen=True)
class IndexedChunk:
    category: str
    heading_path: str
    text: str
    logical_chunk_id: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    source_path: str
    source_hash: str
    title: str
    chunks: list[IndexedChunk]
    facts: list[SectionFact]


@dataclass(frozen=True)
class SectionFact:
    """A structured fact about a section that exists but carries no chunk text.

    Stored in the `section_facts` table rather than `chunks` so that
    "present but unavailable" information is queryable without polluting
    FTS or embedding indexes with useless placeholder text.
    """

    heading_path: str
    fact: str  # "present_but_unavailable"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    """Rough token estimate: 4 chars per token (English prose approximation)."""
    return estimate_tokens_from_chars(len(text))


def estimate_tokens_from_chars(char_count: int) -> int:
    """Estimate tokens from a non-negative character count."""
    if char_count < 0:
        raise ValueError("char_count must be non-negative")
    return max(1, char_count // 4)


def split_text_to_limit(text: str, *, max_chars: int = CHUNK_TARGET_CHARS) -> list[str]:
    """Split text at table separators or whitespace without exceeding max_chars."""
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    remaining = text.strip()
    pieces: list[str] = []
    while len(remaining) > max_chars:
        separator_cut = remaining.rfind(" | ", 0, max_chars + 1)
        whitespace_cut = remaining.rfind(" ", 0, max_chars + 1)
        cut = max(separator_cut, whitespace_cut)
        if cut < 1:
            cut = max_chars
        piece = remaining[:cut].strip()
        if piece:
            pieces.append(piece)
        remaining = remaining[cut:].lstrip(" |").strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def bound_chunks(chunks: list[IndexedChunk]) -> list[IndexedChunk]:
    """Enforce the configured target and hard maximum on every indexed chunk."""
    bounded: list[IndexedChunk] = []
    for chunk in chunks:
        pieces = split_text_to_limit(chunk.text)
        for part_index, text in enumerate(pieces, start=1):
            if len(text) > CHUNK_HARD_MAX_CHARS:
                raise ValueError("chunk splitting produced text above CHUNK_HARD_MAX_CHARS")
            logical_chunk_id = chunk.logical_chunk_id
            if logical_chunk_id is not None and len(pieces) > 1:
                logical_chunk_id = f"{logical_chunk_id}__PART_{part_index}"
            bounded.append(
                IndexedChunk(
                    category=chunk.category,
                    heading_path=chunk.heading_path,
                    text=text,
                    logical_chunk_id=logical_chunk_id,
                )
            )
    return bounded


ColumnChunkFactory = Callable[[Tag, str, str], list[IndexedChunk]]


def extract_chunks(
    html: str,
    *,
    fallback_title: str,
    column_chunk_factory: ColumnChunkFactory | None = None,
) -> tuple[str, list[IndexedChunk], list[SectionFact]]:
    """Extract Clarity-style documentation sections from one HTML file.

    Returns (title, chunks, facts).  Facts record sections that are present in
    the document structure but carry no data (NA spans, None siblings).  They
    are stored separately so downstream consumers can answer "does this field
    exist?" without polluting FTS or embedding indexes.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "template", "nav", "footer", "header"]):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else fallback_title
    chunks: list[IndexedChunk] = []
    facts: list[SectionFact] = []
    saw_structured_section = False
    for content_div in soup.find_all("div", id="oContent"):
        header = content_div.find_previous("div", class_="header")
        document_title = header.get_text(" ", strip=True) if header else title

        metadata = content_div.find("table", class_="KeyValue")
        if metadata is not None:
            text = extract_table_content(metadata)
            if text:
                chunks.append(IndexedChunk("metadata", document_title, text))

        for subheader in content_div.find_all("table", class_="SubHeader3"):
            saw_structured_section = True
            section_cell = subheader.find("td", id=True)
            section = (
                str(section_cell.get("id", "unnamed")).strip("_")
                if section_cell is not None
                else "unnamed"
            )
            heading = f"{document_title} > {section}"
            value = subheader.find_next_sibling()
            if value is None:
                facts.append(
                    SectionFact(
                        heading_path=heading,
                        fact="present_but_unavailable",
                    )
                )
                continue
            if isinstance(value, Tag) and value.name == "span" and "NA" in css_classes(value):
                facts.append(SectionFact(heading_path=heading, fact="present_but_unavailable"))
            elif isinstance(value, Tag) and value.name == "table":
                category = classify_table(value)
                is_column_information = section.replace("-", " ").casefold() == "column information"
                if (
                    category == "column_info"
                    and is_column_information
                    and column_chunk_factory is not None
                ):
                    chunks.extend(column_chunk_factory(value, document_title, section))
                else:
                    chunks.extend(table_to_chunks(category, heading, value))
            elif str(value).strip() == "None":
                facts.append(SectionFact(heading_path=heading, fact="present_but_unavailable"))

    if not chunks and not saw_structured_section:
        text = soup.get_text(" ", strip=True)
        if text:
            chunks.append(IndexedChunk("document", title, text))
    return title, bound_chunks(chunks), facts


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
    for row in owned_rows(table):
        row_text: list[str] = []
        for cell in owned_cells(row):
            text = owned_cell_text(cell)
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

    for row in owned_rows(table):
        row_text: list[str] = []
        is_header = False
        for cell in owned_cells(row):
            text = owned_cell_text(cell)
            if not text:
                continue
            if cell.name == "th":
                is_header = True
            if "T1Head" in css_classes(cell):
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
        return (
            [IndexedChunk(category=category, heading_path=heading, text=header_str)]
            if header_str
            else []
        )

    chunks: list[IndexedChunk] = []
    batch: list[str] = []
    batch_chars = len(header_str)

    for row in data_rows:
        row_chars = len(row) + 3  # 3 for the " | " separator
        if batch and batch_chars + row_chars > CHUNK_TARGET_CHARS:
            parts = ([header_str] + batch) if header_str else batch
            chunks.append(
                IndexedChunk(category=category, heading_path=heading, text=" | ".join(parts))
            )
            batch = []
            batch_chars = len(header_str)
        batch.append(row)
        batch_chars += row_chars

    if batch:
        parts = ([header_str] + batch) if header_str else batch
        chunks.append(IndexedChunk(category=category, heading_path=heading, text=" | ".join(parts)))

    return bound_chunks(chunks)


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS nodes;
        DROP TABLE IF EXISTS section_facts;
        DROP TABLE IF EXISTS chunks_fts;
        DROP TABLE IF EXISTS chunks;
        DROP TABLE IF EXISTS docs;
        DROP TABLE IF EXISTS index_metadata;

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

        CREATE TABLE section_facts (
            doc_id TEXT NOT NULL REFERENCES docs(doc_id),
            heading_path TEXT NOT NULL,
            fact TEXT NOT NULL,
            PRIMARY KEY (doc_id, heading_path)
        );

        CREATE TABLE nodes (
            node_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES docs(doc_id),
            parent_id TEXT REFERENCES nodes(node_id),
            depth INTEGER NOT NULL CHECK (depth >= 0),
            position INTEGER NOT NULL CHECK (position >= 0),
            node_type TEXT NOT NULL CHECK (node_type IN ('document', 'section', 'leaf')),
            title TEXT NOT NULL,
            heading_path TEXT NOT NULL,
            text TEXT NOT NULL,
            summary TEXT NOT NULL,
            token_count INTEGER NOT NULL CHECK (token_count >= 1),
            chunk_id TEXT UNIQUE REFERENCES chunks(chunk_id),
            UNIQUE (parent_id, position)
        );

        CREATE INDEX nodes_by_document ON nodes(document_id, depth, position);
        CREATE INDEX nodes_by_parent ON nodes(parent_id, position);
        """
    )


def parse_one_document(
    html_path: Path,
    *,
    corpus_root: Path,
) -> ParsedDocument:
    html_bytes = html_path.read_bytes()
    html = html_bytes.decode("utf-8", errors="replace")
    source_hash = hashlib.sha256(html_bytes).hexdigest()
    source_path = normalized_source_path(html_path, corpus_root)

    def column_chunks(
        table: Tag,
        table_name: str,
        section_name: str,
    ) -> list[IndexedChunk]:
        records, _warnings = parse_column_records(
            table,
            source_file=source_path,
            source_hash=source_hash,
            table_name=table_name,
            section_name=section_name.replace("-", " "),
        )
        return [
            IndexedChunk(
                category="column_info",
                heading_path=f"{record.table_name} > Column-Information > {record.column_name}",
                text=record.text,
                logical_chunk_id=record.chunk_id,
            )
            for record in records
        ]

    title, chunks, facts = extract_chunks(
        html,
        fallback_title=html_path.stem,
        column_chunk_factory=column_chunks,
    )

    return ParsedDocument(
        source_path=source_path,
        source_hash=source_hash,
        title=title,
        chunks=chunks,
        facts=facts,
    )


def parse_one_document_task(args: tuple[Path, Path]) -> ParsedDocument:
    html_path, corpus_root = args
    return parse_one_document(html_path, corpus_root=corpus_root)


def insert_parsed_document(
    conn: sqlite3.Connection,
    parsed: ParsedDocument,
) -> tuple[str, int]:
    # doc_id is path-only so it survives content changes across re-indexing runs.
    document_id = sha256_text(parsed.source_path)

    conn.execute(
        "INSERT INTO docs (doc_id, source_path, title, source_hash) VALUES (?, ?, ?, ?)",
        (document_id, parsed.source_path, parsed.title, parsed.source_hash),
    )

    root_node_id = sha256_text(f"hierarchy:document:{document_id}")
    root_summary = f"Documentation for {parsed.title}."
    conn.execute(
        """INSERT INTO nodes
           (node_id, document_id, parent_id, depth, position, node_type, title,
            heading_path, text, summary, token_count, chunk_id)
           VALUES (?, ?, NULL, 0, 0, 'document', ?, ?, '', ?, ?, NULL)""",
        (
            root_node_id,
            document_id,
            parsed.title,
            json.dumps([parsed.title], separators=(",", ":")),
            root_summary,
            estimate_tokens(root_summary),
        ),
    )

    section_nodes: dict[tuple[str, ...], str] = {}
    next_position: dict[str, int] = {root_node_id: 0}

    def ensure_section(path: tuple[str, ...]) -> str:
        existing = section_nodes.get(path)
        if existing is not None:
            return existing
        parent_id = root_node_id if len(path) == 1 else ensure_section(path[:-1])
        node_id = sha256_text(
            "hierarchy:section:" + document_id + ":" + json.dumps(path, separators=(",", ":"))
        )
        position = next_position.get(parent_id, 0)
        next_position[parent_id] = position + 1
        next_position[node_id] = 0
        full_path = [parsed.title, *path]
        summary = f"{path[-1]} section in {parsed.title}."
        conn.execute(
            """INSERT INTO nodes
               (node_id, document_id, parent_id, depth, position, node_type, title,
                heading_path, text, summary, token_count, chunk_id)
               VALUES (?, ?, ?, ?, ?, 'section', ?, ?, '', ?, ?, NULL)""",
            (
                node_id,
                document_id,
                parent_id,
                len(path),
                position,
                path[-1],
                json.dumps(full_path, separators=(",", ":")),
                summary,
                estimate_tokens(summary),
            ),
        )
        section_nodes[path] = node_id
        return node_id

    # occurrence distinguishes split chunks that share a heading/category.
    occurrence_counter: dict[tuple[str, str], int] = {}
    for chunk_index, chunk in enumerate(parsed.chunks):
        text_hash = sha256_text(chunk.text)
        occ_key = (chunk.heading_path, chunk.category)
        occurrence = occurrence_counter.get(occ_key, 0)
        occurrence_counter[occ_key] = occurrence + 1
        chunk_id = chunk.logical_chunk_id or sha256_text(
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

        heading_parts = tuple(
            part.strip() for part in chunk.heading_path.split(">") if part.strip()
        )
        if heading_parts and heading_parts[0].casefold() == parsed.title.casefold():
            heading_parts = heading_parts[1:]
        parent_id = root_node_id if not heading_parts else ensure_section(heading_parts)
        leaf_position = next_position.get(parent_id, 0)
        next_position[parent_id] = leaf_position + 1
        leaf_node_id = sha256_text(f"hierarchy:leaf:{chunk_id}")
        leaf_path = [parsed.title, *heading_parts]
        leaf_title = heading_parts[-1] if heading_parts else chunk.category
        conn.execute(
            """INSERT INTO nodes
               (node_id, document_id, parent_id, depth, position, node_type, title,
                heading_path, text, summary, token_count, chunk_id)
               VALUES (?, ?, ?, ?, ?, 'leaf', ?, ?, ?, ?, ?, ?)""",
            (
                leaf_node_id,
                document_id,
                parent_id,
                len(heading_parts) + 1,
                leaf_position,
                leaf_title,
                json.dumps(leaf_path, separators=(",", ":")),
                chunk.text,
                chunk.text,
                estimate_tokens(chunk.text),
                chunk_id,
            ),
        )

        conn.execute(
            """INSERT INTO chunks_fts
               (chunk_id, source_path, title, category, heading_path, text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                chunk_id,
                parsed.source_path,
                parsed.title,
                chunk.category,
                chunk.heading_path,
                chunk.text,
            ),
        )

    for fact in parsed.facts:
        conn.execute(
            "INSERT OR IGNORE INTO section_facts (doc_id, heading_path, fact) VALUES (?, ?, ?)",
            (document_id, fact.heading_path, fact.fact),
        )

    return parsed.source_hash, len(parsed.chunks)


def batched(items: list[Path], size: int) -> list[list[Path]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _report_progress(processed: int, total: int, chunks: int) -> None:
    """Print periodic progress so a long indexing run is not silent."""
    if processed % PROGRESS_INTERVAL == 0:
        print(
            f"Processed {processed:,}/{total:,} documents ({chunks:,} chunks)",
            flush=True,
        )


def build_index(
    input_path: Path,
    db_path: Path,
    *,
    limit: int | None = None,
    workers: int | None = None,
    batch_size: int = 500,
) -> tuple[str, int, int]:
    html_files = discover_html_files(input_path)

    if limit is not None:
        html_files = html_files[:limit]

    if not html_files:
        raise ValueError(f"No HTML files found under {input_path}")

    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    requested_workers = workers if workers is not None else min(os.cpu_count() or 1, 8)
    if requested_workers < 1:
        raise ValueError("workers must be at least 1")
    worker_count = min(requested_workers, len(html_files))

    corpus_root = input_path if input_path.is_dir() else input_path.parent

    db_path.parent.mkdir(parents=True, exist_ok=True)
    source_fingerprints: list[dict[str, str]] = []
    total_chunks = 0
    processed_documents = 0

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)

        for batch in batched(html_files, batch_size):
            tasks = [(html_file, corpus_root) for html_file in batch]

            if worker_count == 1:
                parsed_documents = (parse_one_document_task(task) for task in tasks)
                for parsed in parsed_documents:
                    source_hash, chunk_count = insert_parsed_document(conn, parsed)
                    source_fingerprints.append(
                        {
                            "source_path": parsed.source_path,
                            "source_hash": source_hash,
                        }
                    )
                    total_chunks += chunk_count
                    processed_documents += 1
                    _report_progress(processed_documents, len(html_files), total_chunks)
            else:
                with ProcessPoolExecutor(max_workers=worker_count) as pool:
                    # Parsed documents can contain many chunks. Bound the number of
                    # completed results waiting in memory instead of submitting the
                    # entire batch at once.
                    parsed_documents = pool.map(
                        parse_one_document_task,
                        tasks,
                        buffersize=max(worker_count * 2, 1),
                    )
                    for parsed in parsed_documents:
                        source_hash, chunk_count = insert_parsed_document(conn, parsed)
                        source_fingerprints.append(
                            {
                                "source_path": parsed.source_path,
                                "source_hash": source_hash,
                            }
                        )
                        total_chunks += chunk_count
                        processed_documents += 1
                        _report_progress(processed_documents, len(html_files), total_chunks)

            conn.commit()

        total_section_facts = conn.execute("SELECT COUNT(*) FROM section_facts").fetchone()[0]
        total_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

        version_manifest = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "parser_version": PARSER_VERSION,
            "chunker_version": CHUNKER_VERSION,
            "chunk_target_chars": CHUNK_TARGET_CHARS,
            "chunk_hard_max_chars": CHUNK_HARD_MAX_CHARS,
            "sources": source_fingerprints,
        }
        index_version = sha256_text(
            json.dumps(version_manifest, sort_keys=True, separators=(",", ":"))
        )
        metadata = {
            "index_version": index_version,
            "schema_version": INDEX_SCHEMA_VERSION,
            "parser_version": PARSER_VERSION,
            "chunker_version": CHUNKER_VERSION,
            "chunk_target_chars": str(CHUNK_TARGET_CHARS),
            "chunk_hard_max_chars": str(CHUNK_HARD_MAX_CHARS),
            "doc_count": str(len(html_files)),
            "chunk_count": str(total_chunks),
            "section_fact_count": str(total_section_facts),
            "hierarchy_node_count": str(total_nodes),
        }
        conn.executemany(
            "INSERT INTO index_metadata (key, value) VALUES (?, ?)",
            metadata.items(),
        )

    return index_version, len(html_files), total_chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="Index one approved HTML documentation file.")
    parser.add_argument("input_path", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--bs", type=int, default=500)
    parser.add_argument("--db", type=Path, default=Path(".local/rag/index.sqlite"))
    args = parser.parse_args()
    if args.input_path.is_file() and args.input_path.suffix.lower() not in {".html", ".htm"}:
        parser.error("input_path must be an HTML file or a directory")

    version, doc_count, chunk_count = build_index(
        args.input_path,
        args.db,
        limit=args.limit,
        workers=args.workers,
        batch_size=args.bs,
    )

    print(f"Indexed {doc_count} docs / {chunk_count} chunks into {args.db} (version={version})")


if __name__ == "__main__":
    main()
