from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from retrieval.chunk_models import SplitChunkRecord
from retrieval.genq.metadata_to_chunks import (
    CONVERSION_VERSION,
    SPLIT_VERSION,
    ConversionReport,
    MetadataConversionConfig,
    convert_metadata_to_split_chunks,
)
from retrieval.metadata_extractor import EXTRACTOR_VERSION, MetadataEmbeddingRecord


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _record(table_name: str, description: str | None = None) -> MetadataEmbeddingRecord:
    source_path = f"{table_name}.html"
    if description is not None:
        embedding_text = f"Table: {table_name}\nDescription: {description}"
        description_status = "present"
    else:
        embedding_text = f"Table: {table_name}"
        description_status = "missing"
    return MetadataEmbeddingRecord(
        embedding_id=f"table:{_hash(source_path)}",
        document_id=_hash(source_path),
        source_path=source_path,
        source_hash=_hash(source_path + " source"),
        table_name=table_name,
        title=f"{table_name} - Dictionary",
        metadata_chunk_id=_hash(f"{source_path}:0"),
        metadata_text_hash=_hash(f"metadata for {table_name}"),
        description_status=description_status,
        description=description,
        embedding_text=embedding_text,
        embedding_text_hash=_hash(embedding_text),
        index_version="test-v1",
        extractor_version=EXTRACTOR_VERSION,
    )


def _write_records(path: Path, records: list[MetadataEmbeddingRecord]) -> None:
    path.write_text(
        "\n".join(
            json.dumps(r.model_dump(), sort_keys=True, ensure_ascii=False) for r in records
        )
        + "\n",
        encoding="utf-8",
    )


def test_converts_metadata_to_valid_split_chunks(tmp_path: Path) -> None:
    input_path = tmp_path / "metadata.jsonl"
    output_path = tmp_path / "chunks.jsonl"
    report_path = tmp_path / "report.json"
    records = [
        _record("PAT_ENC", "Patient encounters table."),
        _record("CLARITY_ADT", "ADT event history."),
        _record("ZC_EMPTY"),
    ]
    _write_records(input_path, records)

    report = convert_metadata_to_split_chunks(
        MetadataConversionConfig(input_path, output_path, report_path)
    )

    assert report.output_record_count == 3
    assert report.conversion_version == CONVERSION_VERSION
    assert report.split_version == SPLIT_VERSION
    assert sum(report.source_counts_by_split.values()) == 3

    chunks = [
        SplitChunkRecord.model_validate_json(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(chunks) == 3
    by_table = {c.table_name: c for c in chunks}
    enc = by_table["PAT_ENC"]
    assert enc.chunk_type == "table_metadata"
    assert enc.text == "Table: PAT_ENC\nDescription: Patient encounters table."
    assert enc.text_hash == _hash(enc.text)
    assert enc.source_file == "PAT_ENC.html"
    assert enc.split in ("train", "validation", "test")

    empty = by_table["ZC_EMPTY"]
    assert empty.text == "Table: ZC_EMPTY"


def test_deterministic_across_runs(tmp_path: Path) -> None:
    input_path = tmp_path / "metadata.jsonl"
    output_path = tmp_path / "chunks.jsonl"
    report_path = tmp_path / "report.json"
    _write_records(input_path, [_record("TABLE_A", "Description A.")])

    first = convert_metadata_to_split_chunks(
        MetadataConversionConfig(input_path, output_path, report_path)
    )
    first_output = output_path.read_bytes()
    second = convert_metadata_to_split_chunks(
        MetadataConversionConfig(input_path, output_path, report_path)
    )

    assert first == second
    assert output_path.read_bytes() == first_output


def test_limit_caps_output(tmp_path: Path) -> None:
    input_path = tmp_path / "metadata.jsonl"
    output_path = tmp_path / "chunks.jsonl"
    report_path = tmp_path / "report.json"
    _write_records(input_path, [_record(f"TABLE_{i}") for i in range(10)])

    report = convert_metadata_to_split_chunks(
        MetadataConversionConfig(input_path, output_path, report_path, limit=3)
    )

    assert report.output_record_count == 3


def test_config_rejects_overlapping_paths(tmp_path: Path) -> None:
    db = tmp_path / "in.jsonl"
    config = MetadataConversionConfig(db, db, tmp_path / "report.json")
    with pytest.raises(ValueError, match="must be different"):
        config.validate()


def test_stored_report_round_trips(tmp_path: Path) -> None:
    input_path = tmp_path / "metadata.jsonl"
    output_path = tmp_path / "chunks.jsonl"
    report_path = tmp_path / "report.json"
    _write_records(input_path, [_record("ROUND_TRIP", "Test.")])

    report = convert_metadata_to_split_chunks(
        MetadataConversionConfig(input_path, output_path, report_path)
    )
    stored = ConversionReport.model_validate_json(report_path.read_text(encoding="utf-8"))

    assert stored == report
