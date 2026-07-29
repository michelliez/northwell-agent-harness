"""Parse Epic Clarity HTML documentation into validated GenQ corpus JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import Tag

from retrieval.genq.chunk_models import ChunkRecord, ChunkType, ParseReport
from retrieval.indexer import (
    css_classes,
    discover_html_files,
    normalized_source_path,
    owned_cell_text,
)

LOGGER = logging.getLogger(__name__)
PARSER_VERSION = "epic-genq-html-v3"
DEFAULT_LIMIT = 100


@dataclass(frozen=True)
class ParserConfig:
    """Configuration for a deterministic Stage 1 proof-of-concept parse."""

    input_path: Path
    output_path: Path
    report_path: Path
    limit: int | None = DEFAULT_LIMIT

    def validate(self) -> None:
        """Fail early for unsafe or nonsensical parser settings."""
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be at least 1 or None")
        if self.output_path == self.report_path:
            raise ValueError("output_path and report_path must be different")


@dataclass(frozen=True)
class ParsedSource:
    """Records and inspection facts extracted from one source file."""

    records: list[ChunkRecord]
    unavailable_section_count: int
    warnings: list[str]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _clean_text(value: str) -> str:
    return " ".join(value.split()).strip()


def _identifier(value: str) -> str:
    """Turn an Epic name into a readable, platform-independent ID component."""
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").upper()
    return normalized or "UNNAMED"


def _owned_rows(table: Tag) -> list[Tag]:
    return [row for row in table.find_all("tr") if row.find_parent("table") is table]


def _owned_cells(row: Tag) -> list[Tag]:
    return [cell for cell in row.find_all(["th", "td"]) if cell.find_parent("tr") is row]


def _direct_cell_text(cell: Tag) -> str:
    """Read a cell once while still including values held in a nested one-cell table."""
    return owned_cell_text(cell)


def _metadata_cell_text(cell: Tag) -> str:
    """Read only text owned by a metadata cell despite Epic's unclosed td tags."""
    return owned_cell_text(cell)


def _table_name(soup: BeautifulSoup, fallback: str) -> str:
    content = soup.find("div", id="oContent")
    header = content.find_previous("div", class_="header") if content else None
    if isinstance(header, Tag):
        name = _clean_text(header.get_text(" ", strip=True))
        if name:
            return name
    return fallback


def _metadata_pairs(table: Tag) -> list[tuple[str, str]]:
    """Extract direct KeyValue label/value pairs without flattening whole rows."""
    pairs: list[tuple[str, str]] = []
    for row in _owned_rows(table):
        cells = _owned_cells(row)
        for index, cell in enumerate(cells):
            if "T1Head" not in css_classes(cell):
                continue
            label = _metadata_cell_text(cell).rstrip(":").strip()
            value = ""
            for candidate in cells[index + 1 :]:
                if "T1Value" in css_classes(candidate):
                    value = _metadata_cell_text(candidate)
                    break
                if "T1Head" in css_classes(candidate):
                    break
            if label and value:
                pairs.append((label, value))
    return pairs


def _make_record(
    *,
    source_file: str,
    source_hash: str,
    table_name: str,
    chunk_type: ChunkType,
    section_name: str,
    text: str,
    column_name: str | None = None,
) -> ChunkRecord:
    identity = column_name if column_name else section_name
    chunk_id = f"{_identifier(table_name)}__{_identifier(chunk_type)}__{_identifier(identity)}"
    clean_text = _clean_text(text)
    return ChunkRecord(
        chunk_id=chunk_id,
        source_file=source_file,
        source_hash=source_hash,
        table_name=table_name,
        column_name=column_name,
        chunk_type=chunk_type,
        section_name=section_name,
        text=clean_text,
        text_hash=_sha256_text(clean_text),
        parser_version=PARSER_VERSION,
    )


def parse_column_records(
    table: Tag,
    *,
    source_file: str,
    source_hash: str,
    table_name: str,
    section_name: str,
) -> tuple[list[ChunkRecord], list[str]]:
    rows = _owned_rows(table)
    if not rows:
        return [], [f"{source_file}: Column Information contains no rows"]

    header_cells = _owned_cells(rows[0])
    headers = [_direct_cell_text(cell) or "Ordinal" for cell in header_cells]
    records: list[ChunkRecord] = []
    warnings: list[str] = []
    index = 1

    while index < len(rows):
        cells = _owned_cells(rows[index])
        texts = [_direct_cell_text(cell) for cell in cells]
        is_definition = len(texts) >= 2 and "T1Head" in css_classes(cells[1])
        if not is_definition:
            index += 1
            continue

        column_name = texts[1]
        attributes = [
            (headers[position] if position < len(headers) else f"Field {position}", value)
            for position, value in enumerate(texts)
            if value and position not in {0, 1}
        ]
        description = ""
        if index + 1 < len(rows):
            next_cells = _owned_cells(rows[index + 1])
            if next_cells and not (len(next_cells) >= 2 and "T1Head" in css_classes(next_cells[1])):
                description = _clean_text(" ".join(_direct_cell_text(cell) for cell in next_cells))
                index += 1

        parts = [f"Table {table_name}.", f"Column {column_name}."]
        parts.extend(f"{label}: {value}." for label, value in attributes)
        if description:
            parts.append(f"Description: {description}")
        else:
            warnings.append(f"{source_file}: column {column_name} has no description")

        records.append(
            _make_record(
                source_file=source_file,
                source_hash=source_hash,
                table_name=table_name,
                column_name=column_name,
                chunk_type="column_definition",
                section_name=section_name,
                text=" ".join(parts),
            )
        )
        index += 1
    return records, warnings


def _generic_table_record(
    table: Tag,
    *,
    source_file: str,
    source_hash: str,
    table_name: str,
    section_name: str,
) -> ChunkRecord | None:
    rows: list[str] = []
    for row in _owned_rows(table):
        values = [_direct_cell_text(cell) for cell in _owned_cells(row)]
        rendered = " | ".join(value for value in values if value)
        if rendered:
            rows.append(rendered)
    if not rows:
        return None

    section_key = section_name.casefold()
    if "primary" in section_key:
        chunk_type: ChunkType = "primary_key"
    elif "index" in section_key:
        chunk_type = "index_information"
    elif "foreign" in section_key:
        chunk_type = "foreign_key"
    elif "dependent" in section_key or "grouped" in section_key:
        chunk_type = "relationship"
    else:
        chunk_type = "section"
    return _make_record(
        source_file=source_file,
        source_hash=source_hash,
        table_name=table_name,
        chunk_type=chunk_type,
        section_name=section_name,
        text=f"Table {table_name}. Section {section_name}. " + " ; ".join(rows),
    )


def parse_epic_html(html_path: Path, *, corpus_root: Path) -> ParsedSource:
    """Parse one Epic HTML file into validated table- and column-level records."""
    html_bytes = html_path.read_bytes()
    html = html_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    source_file = normalized_source_path(html_path, corpus_root)
    source_hash = _sha256_bytes(html_bytes)
    table_name = _table_name(soup, html_path.stem)
    records: list[ChunkRecord] = []
    warnings: list[str] = []
    unavailable = 0

    if "\ufffd" in html:
        warnings.append(f"{source_file}: invalid UTF-8 bytes were replaced")

    content = soup.find("div", id="oContent")
    if not isinstance(content, Tag):
        raise ValueError(f"{source_file}: missing div#oContent")

    metadata = content.find("table", class_="KeyValue")
    if isinstance(metadata, Tag):
        pairs = _metadata_pairs(metadata)
        if pairs:
            rendered = " ".join(f"{label}: {value.rstrip('.')}." for label, value in pairs)
            records.append(
                _make_record(
                    source_file=source_file,
                    source_hash=source_hash,
                    table_name=table_name,
                    chunk_type="table_metadata",
                    section_name="Table Metadata",
                    text=f"Table {table_name}. {rendered}",
                )
            )
        else:
            warnings.append(f"{source_file}: table metadata contains no label/value pairs")

    for section_header in content.find_all("table", class_="SubHeader3"):
        section_cell = section_header.find("td", id=True)
        section_name = (
            _clean_text(section_cell.get_text(" ", strip=True))
            if isinstance(section_cell, Tag)
            else "Unnamed Section"
        )
        value = section_header.find_next_sibling()
        if not isinstance(value, Tag):
            unavailable += 1
            continue
        if value.name == "span" and "NA" in css_classes(value):
            unavailable += 1
            continue
        if value.name != "table":
            warnings.append(f"{source_file}: unsupported content after section {section_name}")
            continue

        if section_name.casefold() == "column information":
            column_records, column_warnings = parse_column_records(
                value,
                source_file=source_file,
                source_hash=source_hash,
                table_name=table_name,
                section_name=section_name,
            )
            records.extend(column_records)
            warnings.extend(column_warnings)
        else:
            record = _generic_table_record(
                value,
                source_file=source_file,
                source_hash=source_hash,
                table_name=table_name,
                section_name=section_name,
            )
            if record is not None:
                records.append(record)

    if not records:
        raise ValueError(f"{source_file}: parsing produced no chunks")
    return ParsedSource(records=records, unavailable_section_count=unavailable, warnings=warnings)


def _write_atomic(path: Path, lines: Iterable[str]) -> None:
    """Replace an artifact only after its complete contents have been written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
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


def build_corpus(config: ParserConfig) -> ParseReport:
    """Parse a deterministic file subset and write JSONL plus an inspection report."""
    config.validate()
    html_files = discover_html_files(config.input_path)
    if config.limit is not None:
        html_files = html_files[: config.limit]
    if not html_files:
        raise ValueError(f"No HTML files found under {config.input_path}")

    corpus_root = config.input_path if config.input_path.is_dir() else config.input_path.parent
    records: list[ChunkRecord] = []
    warnings: list[str] = []
    unavailable_count = 0
    for position, html_path in enumerate(html_files, start=1):
        LOGGER.info("Parsing %s (%d/%d)", html_path.name, position, len(html_files))
        parsed = parse_epic_html(html_path, corpus_root=corpus_root)
        records.extend(parsed.records)
        warnings.extend(parsed.warnings)
        unavailable_count += parsed.unavailable_section_count

    duplicate_ids = [
        chunk_id
        for chunk_id, count in Counter(record.chunk_id for record in records).items()
        if count > 1
    ]
    if duplicate_ids:
        preview = ", ".join(sorted(duplicate_ids)[:5])
        raise ValueError(f"Duplicate logical chunk IDs found: {preview}")

    jsonl_lines = [
        json.dumps(record.model_dump(), sort_keys=True, ensure_ascii=False) for record in records
    ]
    corpus_hash = _sha256_text("\n".join(jsonl_lines) + "\n")
    report = ParseReport(
        parser_version=PARSER_VERSION,
        input_path=str(config.input_path),
        output_path=str(config.output_path),
        requested_limit=config.limit,
        source_file_count=len(html_files),
        chunk_count=len(records),
        chunk_counts_by_type=dict(sorted(Counter(r.chunk_type for r in records).items())),
        unavailable_section_count=unavailable_count,
        warning_count=len(warnings),
        warnings=warnings,
        corpus_hash=corpus_hash,
    )
    _write_atomic(config.output_path, jsonl_lines)
    _write_atomic(
        config.report_path,
        [json.dumps(report.model_dump(), indent=2, sort_keys=True, ensure_ascii=False)],
    )
    LOGGER.info("Wrote %d chunks from %d files", len(records), len(html_files))
    return report


def main() -> None:
    """Run the Stage 1 parser from the command line."""
    parser = argparse.ArgumentParser(
        description="Parse Epic HTML into validated, column-level GenQ corpus JSONL."
    )
    parser.add_argument("input_path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        report = build_corpus(
            ParserConfig(
                input_path=args.input_path,
                output_path=args.output,
                report_path=args.report,
                limit=args.limit,
            )
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"Parsed {report.source_file_count} files into {report.chunk_count} chunks "
        f"({report.warning_count} warnings); corpus_hash={report.corpus_hash}"
    )


if __name__ == "__main__":
    main()
