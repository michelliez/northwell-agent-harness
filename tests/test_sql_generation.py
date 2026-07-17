import pytest
from pydantic import ValidationError

from harness_spike.mcp_servers import sql_generation


@pytest.mark.parametrize(
    "payload",
    [
        {
            "sql": "SELECT COUNT(*) FROM appointments",
            "tables": [],
            "notes": [],
            "refused": False,
            "reason": None,
        },
        {
            "sql": None,
            "tables": ["appointments"],
            "notes": [],
            "refused": True,
            "reason": "unsafe",
        },
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
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        sql_generation.SqlGenerationResult.model_validate(payload)


def test_generation_result_accepts_valid_sql_state() -> None:
    result = sql_generation.SqlGenerationResult.model_validate(
        {
            "sql": "SELECT COUNT(*) FROM appointments",
            "tables": ["appointments"],
            "notes": [],
            "refused": False,
            "reason": None,
        }
    )

    assert result.sql == "SELECT COUNT(*) FROM appointments"
