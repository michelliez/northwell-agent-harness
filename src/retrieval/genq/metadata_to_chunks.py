"""Convert table-metadata embedding records to SplitChunkRecord JSONL.

Reads the MetadataEmbeddingRecord JSONL produced by the metadata extractor and
writes SplitChunkRecord-compatible JSONL that the existing query generation
pipeline can consume without modification.  Splits are assigned using the same
SHA-256 scheme as ``evals.dataset_split``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from evals.dataset_split import SplitConfig, assign_source_split
from retrieval.chunk_models import SplitChunkRecord
from retrieval.metadata_extractor import MetadataEmbeddingRecord, _write_atomic

CONVERSION_VERSION = "metadata-to-genq-v1"
SPLIT_VERSION = "metadata-source-hash-split-v1"
DEFAULT_SEED = "epic-genq-v1"


class ConversionReport(BaseModel):
    """Audit record for one metadata-to-chunk conversion run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conversion_version: str
    split_version: str
    input_path: str
    output_path: str
    report_path: str
    seed: str = Field(min_length=1)
    input_record_count: int = Field(ge=0)
    output_record_count: int = Field(ge=0)
    skipped_short_count: int = Field(ge=0)
    source_counts_by_split: dict[str, int]
    description_status_counts: dict[str, int]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class MetadataConversionConfig:
    """Paths and settings for one conversion run."""

    input_path: Path
    output_path: Path
    report_path: Path
    seed: str = DEFAULT_SEED
    limit: int | None = None

    def validate(self) -> None:
        resolved = {
            "input_path": self.input_path.resolve(),
            "output_path": self.output_path.resolve(),
            "report_path": self.report_path.resolve(),
        }
        if len(set(resolved.values())) != len(resolved):
            raise ValueError("input_path, output_path, and report_path must be different")
        if not self.seed.strip():
            raise ValueError("seed must not be blank")
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be at least 1")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _to_split_chunk(
    record: MetadataEmbeddingRecord,
    split_config: SplitConfig,
) -> SplitChunkRecord:
    split = assign_source_split(record.source_path, split_config)
    return SplitChunkRecord(
        chunk_id=record.embedding_id,
        source_file=record.source_path,
        source_hash=record.source_hash,
        table_name=record.table_name,
        column_name=None,
        chunk_type="table_metadata",
        section_name=record.table_name,
        text=record.embedding_text,
        text_hash=record.embedding_text_hash,
        parser_version=record.extractor_version,
        split=split,
        split_version=SPLIT_VERSION,
    )


def convert_metadata_to_split_chunks(
    config: MetadataConversionConfig,
) -> ConversionReport:
    """Read MetadataEmbeddingRecord JSONL, assign splits, write SplitChunkRecord JSONL."""
    config.validate()
    input_bytes = config.input_path.read_bytes()
    input_hash = hashlib.sha256(input_bytes).hexdigest()
    lines = input_bytes.decode("utf-8").splitlines()
    records = [MetadataEmbeddingRecord.model_validate_json(line) for line in lines if line.strip()]

    if config.limit is not None:
        records = records[: config.limit]

    split_config = SplitConfig(
        input_path=config.input_path,
        output_path=config.output_path,
        report_path=config.report_path,
        seed=config.seed,
    )

    split_counts: Counter[str] = Counter()
    description_counts: Counter[str] = Counter()
    output_lines: list[str] = []
    for record in records:
        chunk = _to_split_chunk(record, split_config)
        split_counts[chunk.split] += 1
        description_counts[record.description_status] += 1
        output_lines.append(
            json.dumps(chunk.model_dump(), sort_keys=True, ensure_ascii=False)
        )

    output_hash = _sha256_text("\n".join(output_lines) + "\n") if output_lines else _sha256_text("")

    report = ConversionReport(
        conversion_version=CONVERSION_VERSION,
        split_version=SPLIT_VERSION,
        input_path=str(config.input_path),
        output_path=str(config.output_path),
        report_path=str(config.report_path),
        seed=config.seed,
        input_record_count=len(lines),
        output_record_count=len(output_lines),
        skipped_short_count=0,
        source_counts_by_split=dict(split_counts),
        description_status_counts=dict(description_counts),
        input_hash=input_hash,
        output_hash=output_hash,
    )

    _write_atomic(config.output_path, output_lines)
    _write_atomic(
        config.report_path,
        [json.dumps(report.model_dump(), indent=2, sort_keys=True, ensure_ascii=False)],
    )
    return report


def main() -> None:
    """Convert metadata embedding records to GenQ-compatible split chunks."""
    parser = argparse.ArgumentParser(
        description="Convert MetadataEmbeddingRecord JSONL to SplitChunkRecord JSONL."
    )
    parser.add_argument("input_path", type=Path, help="MetadataEmbeddingRecord JSONL")
    parser.add_argument("--output", type=Path, required=True, help="Output SplitChunkRecord JSONL")
    parser.add_argument("--report", type=Path, required=True, help="Output JSON audit report")
    parser.add_argument("--seed", default=DEFAULT_SEED, help="Split assignment seed")
    parser.add_argument("--limit", type=int, default=None, help="Optional record cap")
    args = parser.parse_args()
    try:
        report = convert_metadata_to_split_chunks(
            MetadataConversionConfig(
                input_path=args.input_path,
                output_path=args.output,
                report_path=args.report,
                seed=args.seed,
                limit=args.limit,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"Converted {report.output_record_count:,} records "
        f"(train={report.source_counts_by_split.get('train', 0):,}, "
        f"validation={report.source_counts_by_split.get('validation', 0):,}, "
        f"test={report.source_counts_by_split.get('test', 0):,})."
    )


if __name__ == "__main__":
    main()
