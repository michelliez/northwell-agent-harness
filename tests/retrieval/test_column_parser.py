from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from retrieval.chunk_models import ChunkRecord
from retrieval.column_parser import ParserConfig, build_corpus, parse_epic_html

HASH = hashlib.sha256(b"value").hexdigest()

EPIC_HTML = """\
<!doctype html>
<html><head><title>CLARITY_ADT - Clarity Dictionary</title></head><body>
<div class="header">CLARITY_ADT</div>
<div id="oContent">
  <table class="KeyValue"><tbody>
    <tr>
      <td class="T1Head">Type:</td><td class="T1Value">Extracted Table</td>
      <td class="T1Head">Deprecated?</td><td class="T1Value">No</td>
    </tr>
    <tr>
      <td class="T1Head">Description:</td>
      <td class="T1Value"><table class="SubList"><tr><td>ADT events.</td></tr></table></td>
    </tr>
  </tbody></table>
  <table class="SubHeader3"><tr><td id="____Primary-Key____">Primary Key</td></tr></table>
  <table class="List">
    <tr><th>Column Name</th><th>Ordinal Position</th></tr>
    <tr><td>EVENT_ID</td><td>1</td></tr>
  </table>
  <table class="SubHeader3"><tr><td id="____Column-Information____">Column Information</td></tr></table>
  <table class="SubList List"><tbody>
    <tr><th></th><th>Name</th><th>INI</th><th>Item</th><th>Type</th>
        <th>Deprecated?</th><th>EHI Status</th></tr>
    <tr><td class="T1Head">1</td><td class="T1Head">EVENT_ID</td>
        <td>ADT</td><td>.1</td><td>NUMERIC (18,0)</td><td>No</td><td>Exported</td></tr>
    <tr><td></td><td colspan="6"><table class="SubList List">
      <tr><td></td></tr><tr><td>The unique event identifier.</td></tr>
    </table></td></tr>
    <tr><td class="T1Head">2</td><td class="T1Head">EVENT_TYPE_C</td>
        <td>ADT</td><td>100</td><td>INTEGER</td><td>No</td><td>Exported</td></tr>
    <tr><td></td><td colspan="6"><table class="SubList List">
      <tr><td></td></tr><tr><td>The category value for the event type.</td></tr>
    </table></td></tr>
  </tbody></table>
  <table class="SubHeader3"><tr><td id="____Foreign-Key-Information____">Foreign Key Information</td></tr></table>
  <span class="NA">None</span>
</div></body></html>
"""


def write_fixture(path: Path, *, description: str = "ADT events.") -> None:
    path.write_text(EPIC_HTML.replace("ADT events.", description), encoding="utf-8")


def test_parse_emits_one_self_contained_record_per_column(tmp_path: Path) -> None:
    html_path = tmp_path / "CLARITY_ADT.html"
    write_fixture(html_path)

    parsed = parse_epic_html(html_path, corpus_root=tmp_path)
    columns = [record for record in parsed.records if record.chunk_type == "column_definition"]

    assert [record.column_name for record in columns] == ["EVENT_ID", "EVENT_TYPE_C"]
    assert columns[0].chunk_id == "CLARITY_ADT__COLUMN_DEFINITION__EVENT_ID"
    assert "Type: NUMERIC (18,0)." in columns[0].text
    assert "Description: The unique event identifier." in columns[0].text
    assert "EVENT_TYPE_C" not in columns[0].text
    assert parsed.unavailable_section_count == 1


def test_metadata_is_not_duplicated_by_nested_description_table(tmp_path: Path) -> None:
    html_path = tmp_path / "CLARITY_ADT.html"
    write_fixture(html_path)

    parsed = parse_epic_html(html_path, corpus_root=tmp_path)
    metadata = next(record for record in parsed.records if record.chunk_type == "table_metadata")

    assert metadata.text.count("Type: Extracted Table.") == 1
    assert metadata.text.count("Description: ADT events.") == 1


def test_metadata_handles_epic_unclosed_table_cells(tmp_path: Path) -> None:
    html_path = tmp_path / "MALFORMED.html"
    html_path.write_text(
        """<html><body><div class="header">MALFORMED</div><div id="oContent">
        <table class="KeyValue"><tr>
        <td class="T1Head">Type:<td class="T1Value">Extracted Table</td>
        <td class="T1Head">Load Type:<td class="T1Value">UPD</td>
        <td class="T1Head">Deprecated?<td class="T1Value">No</td>
        </td></td></td></tr></table>
        <table class="SubHeader3"><tr><td id="_Columns">Columns</td></tr></table>
        <table class="List"><tr><td>ID</td></tr></table>
        </div></body></html>""",
        encoding="utf-8",
    )

    parsed = parse_epic_html(html_path, corpus_root=tmp_path)
    metadata = next(record for record in parsed.records if record.chunk_type == "table_metadata")

    assert metadata.text == (
        "Table MALFORMED. Type: Extracted Table. Load Type: UPD. Deprecated?: No."
    )


def test_columns_handle_epic_unclosed_rows_without_absorbing_later_columns(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "MALFORMED_COLUMNS.html"
    html_path.write_text(
        """<html><body><div class="header">MALFORMED_COLUMNS</div><div id="oContent">
        <table class="SubHeader3"><tr>
        <td id="____Column-Information____">Column Information
        </table>
        <table class="SubList List"><tbody>
        <tr><th><th>Name<th>INI<th>Item<th>Type
        <tr><td class="T1Head">1<td class="T1Head">FIRST_ID
        <td><table class="SubList"><tr><td>INI</table>
        <td><table class="SubList"><tr><td>.1</table><td>NUMERIC (18,0)
        <tr><td><td colspan="4"><table class="SubList List">
        <tr><td><tr><td>The first identifier.</table>
        <tr><td class="T1Head">2<td class="T1Head">SECOND_DATE
        <td><table class="SubList"><tr><td>INI</table>
        <td><table class="SubList"><tr><td>2</table><td>DATETIME
        <tr><td><td colspan="4"><table class="SubList List">
        <tr><td><tr><td>The second date.</table>
        </tbody></table></div></body></html>""",
        encoding="utf-8",
    )

    parsed = parse_epic_html(html_path, corpus_root=tmp_path)
    columns = [record for record in parsed.records if record.chunk_type == "column_definition"]

    assert [record.column_name for record in columns] == ["FIRST_ID", "SECOND_DATE"]
    assert columns[0].chunk_id == "MALFORMED_COLUMNS__COLUMN_DEFINITION__FIRST_ID"
    assert "SECOND_DATE" not in columns[0].text


def test_logical_id_stays_stable_while_text_hash_detects_change(tmp_path: Path) -> None:
    html_path = tmp_path / "CLARITY_ADT.html"
    write_fixture(html_path, description="First description.")
    first = parse_epic_html(html_path, corpus_root=tmp_path).records[0]

    write_fixture(html_path, description="Corrected description.")
    second = parse_epic_html(html_path, corpus_root=tmp_path).records[0]

    assert first.chunk_id == second.chunk_id
    assert first.text_hash != second.text_hash
    assert first.source_hash != second.source_hash


def test_build_corpus_writes_deterministic_jsonl_and_report(tmp_path: Path) -> None:
    source = tmp_path / "html"
    source.mkdir()
    write_fixture(source / "B_TABLE.html")
    write_fixture(source / "A_TABLE.html")
    output = tmp_path / "chunks.jsonl"
    report_path = tmp_path / "report.json"
    config = ParserConfig(
        input_path=source,
        output_path=output,
        report_path=report_path,
        limit=1,
    )

    first_report = build_corpus(config)
    first_bytes = output.read_bytes()
    second_report = build_corpus(config)

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    stored_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert first_report.source_file_count == 1
    assert first_report.chunk_count == len(rows)
    assert first_report.corpus_hash == second_report.corpus_hash
    assert first_bytes == output.read_bytes()
    assert {row["source_file"] for row in rows} == {"A_TABLE.html"}
    assert stored_report["chunk_counts_by_type"]["column_definition"] == 2


def test_duplicate_logical_ids_fail_instead_of_silently_overwriting(tmp_path: Path) -> None:
    source = tmp_path / "html"
    source.mkdir()
    write_fixture(source / "one.html")
    write_fixture(source / "two.html")

    with pytest.raises(ValueError, match="Duplicate logical chunk IDs"):
        build_corpus(
            ParserConfig(
                input_path=source,
                output_path=tmp_path / "chunks.jsonl",
                report_path=tmp_path / "report.json",
                limit=None,
            )
        )


def test_missing_content_container_has_useful_error(tmp_path: Path) -> None:
    html_path = tmp_path / "broken.html"
    html_path.write_text("<html><body>No structured content</body></html>", encoding="utf-8")

    with pytest.raises(ValueError, match="missing div#oContent"):
        parse_epic_html(html_path, corpus_root=tmp_path)


def _valid_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "chunk_id": "CLARITY_ADT__COLUMN_DEFINITION__EVENT_TYPE_C",
        "source_file": "CLARITY_ADT.html",
        "source_hash": HASH,
        "table_name": "CLARITY_ADT",
        "column_name": "EVENT_TYPE_C",
        "chunk_type": "column_definition",
        "section_name": "Column Information",
        "text": "Table CLARITY_ADT. Column EVENT_TYPE_C. Description: Event type.",
        "text_hash": HASH,
        "parser_version": "epic-genq-html-v3",
    }
    record.update(overrides)
    return record


def test_chunk_record_accepts_complete_column_metadata() -> None:
    record = ChunkRecord.model_validate(_valid_record())

    assert record.column_name == "EVENT_TYPE_C"
    assert record.chunk_type == "column_definition"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chunk_id", ""),
        ("source_hash", "not-a-sha256"),
        ("chunk_type", "made_up_type"),
        ("text", " surrounding whitespace "),
    ],
)
def test_chunk_record_rejects_invalid_fields(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        ChunkRecord.model_validate(_valid_record(**{field: value}))


def test_chunk_record_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ChunkRecord.model_validate(_valid_record(unexpected="value"))
