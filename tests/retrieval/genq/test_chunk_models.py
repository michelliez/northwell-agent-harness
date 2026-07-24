from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from retrieval.genq.chunk_models import ChunkRecord

HASH = hashlib.sha256(b"value").hexdigest()


def valid_record(**overrides: object) -> dict[str, object]:
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
        "parser_version": "epic-genq-html-v1",
    }
    record.update(overrides)
    return record


def test_chunk_record_accepts_complete_column_metadata() -> None:
    record = ChunkRecord.model_validate(valid_record())

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
        ChunkRecord.model_validate(valid_record(**{field: value}))


def test_chunk_record_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ChunkRecord.model_validate(valid_record(unexpected="value"))
