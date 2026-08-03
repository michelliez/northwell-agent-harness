from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from retrieval.index_contract import INDEX_CHUNKER_VERSION, INDEX_SCHEMA_VERSION
from retrieval.indexer import create_schema
from retrieval.metadata_extractor import (
    EXTRACTOR_VERSION,
    MetadataEmbeddingRecord,
    MetadataExtractionReport,
    MetadataExtractorConfig,
    build_embedding_text,
    extract_metadata_corpus,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _create_index(
    path: Path,
    documents: list[tuple[str, str, list[str]]],
) -> None:
    """Create (source path, heading, metadata texts) documents for extraction."""
    with sqlite3.connect(path) as conn:
        create_schema(conn)
        conn.executemany(
            "INSERT INTO index_metadata (key, value) VALUES (?, ?)",
            [
                ("index_version", "test-index-v1"),
                ("schema_version", INDEX_SCHEMA_VERSION),
                ("chunker_version", INDEX_CHUNKER_VERSION),
            ],
        )
        for source_path, heading, metadata_texts in documents:
            document_id = _hash(source_path)
            conn.execute(
                "INSERT INTO docs (doc_id, source_path, title, source_hash) VALUES (?, ?, ?, ?)",
                (
                    document_id,
                    source_path,
                    f"{heading} - Dictionary",
                    _hash(source_path + " source"),
                ),
            )
            for chunk_index, metadata_text in enumerate(metadata_texts):
                conn.execute(
                    """INSERT INTO chunks
                       (chunk_id, doc_id, chunk_index, category, heading_path,
                        text, token_count, text_hash)
                       VALUES (?, ?, ?, 'metadata', ?, ?, ?, ?)""",
                    (
                        _hash(f"{source_path}:{chunk_index}"),
                        document_id,
                        chunk_index,
                        heading,
                        metadata_text,
                        len(metadata_text.split()),
                        _hash(metadata_text),
                    ),
                )


def test_extracts_deterministic_table_records_and_reports_fallbacks(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    output_path = tmp_path / "metadata.jsonl"
    report_path = tmp_path / "report.json"
    _create_index(
        db_path,
        [
            (
                "CLARITY_ADT.html",
                "CLARITY_ADT",
                [
                    "Type:: Extracted Table | Description:: Master table for ADT event history. "
                    "| Replacement Objects:: V_ADT"
                ],
            ),
            ("EMPTY.html", "EMPTY", ["Type:: Extracted Table | Description::   "]),
            ("MISSING.html", "MISSING", ["Type:: Extracted Table"]),
        ],
    )
    config = MetadataExtractorConfig(db_path, output_path, report_path)

    first = extract_metadata_corpus(config)
    first_output = output_path.read_bytes()
    first_report = report_path.read_bytes()
    second = extract_metadata_corpus(config)

    assert first == second
    assert output_path.read_bytes() == first_output
    assert report_path.read_bytes() == first_report
    assert first.source_document_count == 3
    assert first.metadata_chunk_count == 3
    assert first.output_record_count == 3
    assert first.description_record_count == 1
    assert first.fallback_record_count == 2
    assert first.empty_description_count == 1
    assert first.missing_description_count == 1
    assert first.fallback_source_path_sample == ["EMPTY.html", "MISSING.html"]

    records = [
        MetadataEmbeddingRecord.model_validate_json(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    records_by_table = {record.table_name: record for record in records}
    record = records_by_table["CLARITY_ADT"]
    assert record.table_name == "CLARITY_ADT"
    assert record.description_status == "present"
    assert record.description is not None
    assert record.description.endswith("| Replacement Objects:: V_ADT")
    assert record.embedding_text == build_embedding_text(record.table_name, record.description)
    assert record.embedding_text_hash == _hash(record.embedding_text)
    assert record.index_version == "test-index-v1"
    assert record.extractor_version == EXTRACTOR_VERSION
    assert records_by_table["EMPTY"].description_status == "empty"
    assert records_by_table["EMPTY"].description is None
    assert records_by_table["EMPTY"].embedding_text == "Table: EMPTY"
    assert records_by_table["MISSING"].description_status == "missing"
    assert records_by_table["MISSING"].embedding_text == "Table: MISSING"

    stored_report = MetadataExtractionReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    assert stored_report == first
    assert stored_report.output_records_hash == _hash(output_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("documents", "message"),
    [
        ([("MISSING.html", "MISSING", [])], "have no metadata chunk"),
        (
            [("DUPLICATE.html", "DUPLICATE", ["Description:: First", "Description:: Second"])],
            "have multiple metadata chunks",
        ),
    ],
)
def test_rejects_missing_or_multiple_metadata_chunks_without_writing(
    tmp_path: Path,
    documents: list[tuple[str, str, list[str]]],
    message: str,
) -> None:
    db_path = tmp_path / "index.sqlite"
    output_path = tmp_path / "metadata.jsonl"
    report_path = tmp_path / "report.json"
    _create_index(db_path, documents)

    with pytest.raises(ValueError, match=message):
        extract_metadata_corpus(MetadataExtractorConfig(db_path, output_path, report_path))

    assert not output_path.exists()
    assert not report_path.exists()


def test_rejects_source_and_heading_table_disagreement(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    output_path = tmp_path / "metadata.jsonl"
    report_path = tmp_path / "report.json"
    _create_index(db_path, [("PAT_ENC.html", "WRONG_TABLE", ["Description:: Encounters"])])

    with pytest.raises(ValueError, match="disagrees with source table"):
        extract_metadata_corpus(MetadataExtractorConfig(db_path, output_path, report_path))


def test_config_rejects_overlapping_artifact_paths(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    config = MetadataExtractorConfig(db_path, db_path, tmp_path / "report.json")

    with pytest.raises(ValueError, match="must be different"):
        config.validate()
