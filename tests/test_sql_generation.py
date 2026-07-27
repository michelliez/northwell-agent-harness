"""Tests for SqlGenerationResult model validation (no model calls)."""

import pytest
from pydantic import ValidationError

from sql.models import SqlGenerationResult


@pytest.mark.parametrize(
    "payload",
    [
        # sql is set but tables is empty (no tables = invalid)
        {
            "sql": "SELECT COUNT(*) FROM appointments",
            "tables": [],
            "notes": [],
            "refused": False,
            "reason": None,
        },
        # refused=True but sql is not None
        {
            "sql": None,
            "tables": ["appointments"],
            "notes": [],
            "refused": True,
            "reason": "unsafe",
        },
        # sql is None but reason is not the canonical unsupported reason
        {
            "sql": None,
            "tables": [],
            "notes": [],
            "refused": False,
            "reason": "something_else",
        },
    ],
)
def test_generation_result_rejects_incoherent_states(
    payload: dict,
) -> None:
    with pytest.raises(ValidationError):
        SqlGenerationResult.model_validate(payload)


def test_generation_result_accepts_valid_sql_state() -> None:
    result = SqlGenerationResult.model_validate(
        {
            "sql": "SELECT COUNT(*) FROM appointments",
            "tables": ["appointments"],
            "notes": [],
            "refused": False,
            "reason": None,
        }
    )
    assert result.sql is not None
    assert result.tables == ["appointments"]
    assert result.refused is False


def test_generation_result_accepts_refusal() -> None:
    result = SqlGenerationResult.model_validate(
        {
            "sql": None,
            "tables": [],
            "notes": [],
            "refused": True,
            "reason": "patient data requested",
        }
    )
    assert result.refused is True
    assert result.sql is None
    assert result.reason == "patient data requested"


def test_generation_result_accepts_unsupported() -> None:
    result = SqlGenerationResult.model_validate(
        {
            "sql": None,
            "tables": [],
            "notes": [],
            "refused": False,
            "reason": "unsupported_or_ambiguous_request",
        }
    )
    assert result.sql is None
    assert result.refused is False
    assert result.reason == "unsupported_or_ambiguous_request"
