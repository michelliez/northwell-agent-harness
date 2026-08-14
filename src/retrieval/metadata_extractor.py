"""Produce deterministic table-level text records for offline embedding.

This module is deliberately model- and vector-store-independent. It reads the
active RAG SQLite index in read-only mode, selects the single metadata chunk for
each document, and writes one auditable JSONL record per table. Tables without a
description use a table-name-only fallback so dense evaluation never silently
loses a gold document. The output is the input contract for later dense
retrieval experiments; nothing here runs in the request path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from retrieval.search import get_index_version, open_connection

EXTRACTOR_VERSION = "table-metadata-v1"
DESCRIPTION_MARKER = "Description::"
EXCLUSION_SAMPLE_LIMIT = 20
DescriptionStatus = Literal["present", "empty", "missing"]


class MetadataEmbeddingRecord(BaseModel):
    """One stable table-level passage ready to pass to a text encoder."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    embedding_id: str = Field(min_length=1)
    document_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: str = Field(min_length=1)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    table_name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    metadata_chunk_id: str = Field(min_length=1)
    metadata_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    description_status: DescriptionStatus
    description: str | None = Field(default=None, min_length=1)
    embedding_text: str = Field(min_length=1)
    embedding_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_version: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)

    @field_validator(
        "embedding_id",
        "source_path",
        "table_name",
        "title",
        "metadata_chunk_id",
        "embedding_text",
        "index_version",
        "extractor_version",
    )
    @classmethod
    def reject_surrounding_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("must not contain surrounding whitespace")
        return value


class MetadataExtractionReport(BaseModel):
    """Deterministic audit record for one metadata extraction run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    extractor_version: str
    input_db_path: str
    output_path: str
    report_path: str
    index_version: str
    source_document_count: int = Field(ge=0)
    metadata_chunk_count: int = Field(ge=0)
    metadata_document_count: int = Field(ge=0)
    output_record_count: int = Field(ge=0)
    description_record_count: int = Field(ge=0)
    fallback_record_count: int = Field(ge=0)
    missing_description_count: int = Field(ge=0)
    empty_description_count: int = Field(ge=0)
    fallback_source_path_sample: list[str]
    output_records_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class MetadataExtractorConfig:
    """Paths for a single read-only extraction run."""

    db_path: Path
    output_path: Path
    report_path: Path

    def validate(self) -> None:
        resolved = {
            "db_path": self.db_path.resolve(),
            "output_path": self.output_path.resolve(),
            "report_path": self.report_path.resolve(),
        }
        if len(set(resolved.values())) != len(resolved):
            raise ValueError("db_path, output_path, and report_path must be different")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("\t", " ").split()).strip()


def extract_description(metadata_text: str) -> tuple[DescriptionStatus, str | None]:
    """Return explicit description status and normalized text when present."""
    _prefix, marker, remainder = metadata_text.partition(DESCRIPTION_MARKER)
    if not marker:
        return "missing", None
    description = _normalize_text(remainder)
    if not description:
        return "empty", None
    return "present", description


def build_embedding_text(table_name: str, description: str | None) -> str:
    """Create the only text representation encoded by the first dense baseline."""
    if description is None:
        return f"Table: {table_name}"
    return f"Table: {table_name}\nDescription: {description}"


def _table_name(source_path: str, heading_path: str) -> str:
    table_name = PurePosixPath(source_path.replace("\\", "/")).stem.strip()
    heading_name = heading_path.split(" > ", 1)[0].strip()
    if not table_name:
        raise ValueError(f"Cannot derive a table name from source path {source_path!r}")
    if table_name.casefold() != heading_name.casefold():
        raise ValueError(
            f"Metadata heading {heading_name!r} disagrees with source table {table_name!r}"
        )
    return table_name.upper()


def write_atomic(path: Path, lines: Iterable[str]) -> None:
    """Replace an artifact only after its complete contents have been written.

    Public surface: the evaluation repository's dataset and artifact writers
    import this rather than carrying copies.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            for line in lines:
                output.write(line)
                output.write("\n")
        Path(temporary_name).replace(path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _load_index_rows(
    conn: sqlite3.Connection,
) -> tuple[str, list[dict[str, str]], list[dict[str, str | int]]]:
    index_version = get_index_version(conn)
    documents = [
        {
            "document_id": str(row["doc_id"]),
            "source_path": str(row["source_path"]),
            "title": str(row["title"]),
            "source_hash": str(row["source_hash"]),
        }
        for row in conn.execute(
            "SELECT doc_id, source_path, title, source_hash "
            "FROM docs ORDER BY source_path COLLATE BINARY, doc_id"
        )
    ]
    metadata_chunks = [
        {
            "metadata_chunk_id": str(row["chunk_id"]),
            "document_id": str(row["doc_id"]),
            "chunk_index": int(row["chunk_index"]),
            "heading_path": str(row["heading_path"]),
            "metadata_text": str(row["text"]),
            "metadata_text_hash": str(row["text_hash"]),
        }
        for row in conn.execute(
            "SELECT chunk_id, doc_id, chunk_index, heading_path, text, text_hash "
            "FROM chunks WHERE category = 'metadata' "
            "ORDER BY doc_id, chunk_index, chunk_id"
        )
    ]
    return index_version, documents, metadata_chunks


def extract_metadata_corpus(config: MetadataExtractorConfig) -> MetadataExtractionReport:
    """Extract deterministic table-level embedding input from a RAG index."""
    config.validate()
    conn = open_connection(config.db_path)
    try:
        index_version, documents, metadata_chunks = _load_index_rows(conn)
    finally:
        conn.close()

    documents_by_id = {document["document_id"]: document for document in documents}
    if len(documents_by_id) != len(documents):
        raise ValueError("The index contains duplicate document IDs")

    chunks_by_document: dict[str, list[dict[str, str | int]]] = defaultdict(list)
    for chunk in metadata_chunks:
        document_id = str(chunk["document_id"])
        if document_id not in documents_by_id:
            raise ValueError(f"Metadata chunk references unknown document ID {document_id}")
        chunks_by_document[document_id].append(chunk)

    missing_metadata = [
        document["source_path"]
        for document in documents
        if document["document_id"] not in chunks_by_document
    ]
    multiple_metadata = [
        documents_by_id[document_id]["source_path"]
        for document_id, chunks in chunks_by_document.items()
        if len(chunks) != 1
    ]
    if missing_metadata or multiple_metadata:
        details: list[str] = []
        if missing_metadata:
            details.append(
                f"{len(missing_metadata)} document(s) have no metadata chunk: "
                + ", ".join(missing_metadata[:5])
            )
        if multiple_metadata:
            details.append(
                f"{len(multiple_metadata)} document(s) have multiple metadata chunks: "
                + ", ".join(multiple_metadata[:5])
            )
        raise ValueError("; ".join(details))

    records: list[MetadataEmbeddingRecord] = []
    description_status_counts: Counter[str] = Counter()
    fallback_paths: list[str] = []
    table_names: set[str] = set()
    for document in documents:
        chunk = chunks_by_document[document["document_id"]][0]
        table_name = _table_name(document["source_path"], str(chunk["heading_path"]))
        normalized_table_name = table_name.casefold()
        if normalized_table_name in table_names:
            raise ValueError(f"Duplicate table name in metadata corpus: {table_name}")
        table_names.add(normalized_table_name)

        description_status, description = extract_description(str(chunk["metadata_text"]))
        description_status_counts[description_status] += 1
        if description is None and len(fallback_paths) < EXCLUSION_SAMPLE_LIMIT:
            fallback_paths.append(document["source_path"])

        embedding_text = build_embedding_text(table_name, description)
        records.append(
            MetadataEmbeddingRecord(
                embedding_id=f"table:{document['document_id']}",
                document_id=document["document_id"],
                source_path=document["source_path"],
                source_hash=document["source_hash"],
                table_name=table_name,
                title=document["title"],
                metadata_chunk_id=str(chunk["metadata_chunk_id"]),
                metadata_text_hash=str(chunk["metadata_text_hash"]),
                description_status=description_status,
                description=description,
                embedding_text=embedding_text,
                embedding_text_hash=_sha256_text(embedding_text),
                index_version=index_version,
                extractor_version=EXTRACTOR_VERSION,
            )
        )

    if not records:
        raise ValueError("The index contains no table metadata records")

    record_lines = [
        json.dumps(record.model_dump(), sort_keys=True, ensure_ascii=False) for record in records
    ]
    records_hash = _sha256_text("\n".join(record_lines) + "\n")
    report = MetadataExtractionReport(
        extractor_version=EXTRACTOR_VERSION,
        input_db_path=str(config.db_path),
        output_path=str(config.output_path),
        report_path=str(config.report_path),
        index_version=index_version,
        source_document_count=len(documents),
        metadata_chunk_count=len(metadata_chunks),
        metadata_document_count=len(chunks_by_document),
        output_record_count=len(records),
        description_record_count=description_status_counts["present"],
        fallback_record_count=(
            description_status_counts["empty"] + description_status_counts["missing"]
        ),
        missing_description_count=description_status_counts["missing"],
        empty_description_count=description_status_counts["empty"],
        fallback_source_path_sample=fallback_paths,
        output_records_hash=records_hash,
    )

    write_atomic(config.output_path, record_lines)
    write_atomic(
        config.report_path,
        [json.dumps(report.model_dump(), indent=2, sort_keys=True, ensure_ascii=False)],
    )
    return report


def main() -> None:
    """Run the table-metadata extractor from the command line."""
    parser = argparse.ArgumentParser(
        description="Extract deterministic table-level embedding input from a RAG index."
    )
    parser.add_argument("db_path", type=Path, help="SQLite RAG index")
    parser.add_argument("--output", type=Path, required=True, help="Output JSONL records")
    parser.add_argument("--report", type=Path, required=True, help="Output JSON audit report")
    args = parser.parse_args()
    try:
        report = extract_metadata_corpus(
            MetadataExtractorConfig(
                db_path=args.db_path,
                output_path=args.output,
                report_path=args.report,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"Extracted {report.output_record_count:,} table records to {args.output} "
        f"({report.description_record_count:,} descriptions, "
        f"{report.fallback_record_count:,} table-name fallbacks)."
    )


if __name__ == "__main__":
    main()
