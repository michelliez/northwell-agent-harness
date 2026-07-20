from __future__ import annotations

import pytest

from harness_spike.agent_host.tool_registry import (
    ToolContractError,
    canonical_tools_for_server,
    contract_for_tool,
    tools_for_intent,
    validate_live_inventory,
)


def test_route_scope_is_derived_from_one_host_registry() -> None:
    assert tools_for_intent("schema_lookup") == {"get_table_schema"}
    assert tools_for_intent("safe_sql_generation") == {
        "search_tables",
        "get_table_schema",
        "generate_sql",
        "validate_sql",
    }
    assert tools_for_intent("patient_specific_request") == set()


def test_live_inventory_returns_canonical_host_definitions() -> None:
    live = canonical_tools_for_server("catalog")
    live.append(
        {
            "name": "unexpected_tool",
            "description": "This tool is not approved.",
            "input_schema": {"type": "object"},
        }
    )

    result = validate_live_inventory(
        live,
        server="catalog",
        required_tools=frozenset({"search_tables"}),
    )

    assert {tool["name"] for tool in result} == {
        "search_tables",
        "get_table_schema",
    }
    assert result[0]["description"] == contract_for_tool("search_tables").description


def test_live_inventory_rejects_schema_drift() -> None:
    live = canonical_tools_for_server("catalog")
    search = next(tool for tool in live if tool["name"] == "search_tables")
    search["input_schema"] = {
        "type": "object",
        "properties": {"question": {"type": "array"}},
        "required": ["question"],
        "additionalProperties": False,
    }

    with pytest.raises(ToolContractError, match="schema"):
        validate_live_inventory(
            live,
            server="catalog",
            required_tools=frozenset({"search_tables"}),
        )


def test_contract_validates_arguments_and_result_shapes() -> None:
    contract = contract_for_tool("validate_sql", server="sql_validation")
    arguments = contract.validate_input({"sql": "SELECT COUNT(*) FROM appointments"})
    assert arguments["sql"].startswith("SELECT")

    result = contract.validate_result({"allowed": False, "violations": []})
    assert result["allowed"] is False

    with pytest.raises(ToolContractError, match="required"):
        contract.validate_input({"tables": []})

    with pytest.raises(ToolContractError, match="tool result"):
        contract.validate_result({"normalized_sql": "SELECT 1"})
