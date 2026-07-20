"""Host-owned tool contracts for the bounded agent workflows.

MCP discovery is useful for health and compatibility checks, but it is not the
source of execution authority.  The host keeps the small executable surface
here and derives route scopes from the same contract data.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from anthropic.types import ToolParam
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ToolContractError(RuntimeError):
    """Raised when an MCP tool does not match the host-owned contract."""

    def __init__(self, reason: str, *, tool: str | None = None) -> None:
        self.reason = reason
        self.tool = tool
        super().__init__(reason)


class _PermissiveModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class SearchTablesResult(_PermissiveModel):
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class TableSchemaResult(_PermissiveModel):
    table_name: str | None = None
    columns: list[dict[str, Any]] = Field(default_factory=list)


class SqlGenerationResult(_PermissiveModel):
    sql: str | None = None
    tables: list[str] = Field(default_factory=list)
    refused: bool = False


class SqlValidationResult(_PermissiveModel):
    allowed: bool
    disclosure_status: str = "not_evaluated"
    requires_authorized_execution: bool = True
    normalized_sql: str | None = None
    violations: list[dict[str, Any]] = Field(default_factory=list)


ResultValidator = Callable[[Any], Any]


@dataclass(frozen=True)
class ToolContract:
    name: str
    server: str
    description: str
    input_schema: dict[str, Any]
    routes: frozenset[str]
    result_validator: ResultValidator

    def anthropic_tool(self) -> ToolParam:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def validate_input(self, arguments: Any) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ToolContractError(
                "tool arguments must be an object",
                tool=self.name,
            )
        _validate_object_schema(arguments, self.input_schema, self.name)
        return arguments

    def validate_result(self, result: Any) -> Any:
        try:
            return self.result_validator(result)
        except (TypeError, ValueError, ValidationError) as exc:
            raise ToolContractError(
                "tool result did not match the host contract",
                tool=self.name,
            ) from exc


def _validate_object_schema(
    value: Mapping[str, Any], schema: Mapping[str, Any], tool_name: str
) -> None:
    if schema.get("type") != "object":
        return
    required = schema.get("required", [])
    missing = [name for name in required if name not in value]
    if missing:
        raise ToolContractError(
            f"missing required tool arguments: {', '.join(missing)}",
            tool=tool_name,
        )
    if schema.get("additionalProperties") is False:
        properties = schema.get("properties", {})
        unexpected = sorted(set(value) - set(properties))
        if unexpected:
            raise ToolContractError(
                f"unexpected tool arguments: {', '.join(unexpected)}",
                tool=tool_name,
            )
    for name, property_schema in schema.get("properties", {}).items():
        if name not in value:
            continue
        _validate_value_type(value[name], property_schema, tool_name, name)


def _validate_value_type(
    value: Any,
    schema: Mapping[str, Any],
    tool_name: str,
    field_name: str,
) -> None:
    allowed = schema.get("type")
    if allowed == "string" and not isinstance(value, str):
        raise ToolContractError(f"{field_name} must be a string", tool=tool_name)
    if allowed == "array" and not isinstance(value, list):
        raise ToolContractError(f"{field_name} must be an array", tool=tool_name)
    if allowed == "object" and not isinstance(value, dict):
        raise ToolContractError(f"{field_name} must be an object", tool=tool_name)
    if isinstance(allowed, list):
        valid = any(
            option == "null"
            and value is None
            or option == "string"
            and isinstance(value, str)
            or option == "array"
            and isinstance(value, list)
            for option in allowed
        )
        if not valid:
            raise ToolContractError(f"{field_name} has an invalid type", tool=tool_name)


def _model_result(model: type[BaseModel], result: Any) -> Any:
    return model.model_validate(result).model_dump()


def _dict_result(result: Any) -> Any:
    if not isinstance(result, dict):
        raise TypeError("tool result must be an object")
    return result


TOOL_CONTRACTS: dict[str, ToolContract] = {
    "search_tables": ToolContract(
        name="search_tables",
        server="catalog",
        description=(
            "Find candidate tables for a natural-language data question in the dummy catalog."
        ),
        input_schema={
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        },
        routes=frozenset({"table_discovery", "aggregate_definition", "safe_sql_generation"}),
        result_validator=lambda result: _model_result(SearchTablesResult, result),
    ),
    "get_table_schema": ToolContract(
        name="get_table_schema",
        server="catalog",
        description="Get the dummy schema for one candidate table.",
        input_schema={
            "type": "object",
            "properties": {"table_name": {"type": "string"}},
            "required": ["table_name"],
            "additionalProperties": False,
        },
        routes=frozenset({"schema_lookup", "aggregate_definition", "safe_sql_generation"}),
        result_validator=lambda result: _model_result(TableSchemaResult, result),
    ),
    "generate_sql": ToolContract(
        name="generate_sql",
        server="sql_generation",
        description="Generate structured aggregate SQL for the dummy catalog.",
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "schema_context": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                },
            },
            "required": ["question"],
            "additionalProperties": False,
        },
        routes=frozenset({"safe_sql_generation"}),
        result_validator=lambda result: _model_result(SqlGenerationResult, result),
    ),
    "validate_sql": ToolContract(
        name="validate_sql",
        server="sql_validation",
        description="Validate one aggregate SQL query against the dummy catalog.",
        input_schema={
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "tables": {
                    "anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}],
                    "default": None,
                },
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
        routes=frozenset({"safe_sql_generation"}),
        result_validator=lambda result: _model_result(SqlValidationResult, result),
    ),
}


def tools_for_intent(intent: str) -> frozenset[str]:
    """Derive host-owned capability scope from the single contract registry."""
    return frozenset(name for name, contract in TOOL_CONTRACTS.items() if intent in contract.routes)


def contract_for_tool(name: str, *, server: str | None = None) -> ToolContract:
    contract = TOOL_CONTRACTS.get(name)
    if contract is None or (server is not None and contract.server != server):
        raise ToolContractError("tool is not in the host contract registry", tool=name)
    return contract


def canonical_tools_for_server(server: str) -> list[ToolParam]:
    return [
        contract.anthropic_tool()
        for contract in TOOL_CONTRACTS.values()
        if contract.server == server
    ]


def validate_live_inventory(
    tools: list[ToolParam],
    *,
    server: str,
    required_tools: frozenset[str],
) -> list[ToolParam]:
    live_by_name = {str(tool.get("name")): tool for tool in tools if isinstance(tool, dict)}
    server_contracts = {
        name: contract for name, contract in TOOL_CONTRACTS.items() if contract.server == server
    }
    missing = sorted(name for name in required_tools if name not in live_by_name)
    if missing:
        raise ToolContractError(f"required MCP tools are missing: {', '.join(missing)}")

    for name, live_tool in live_by_name.items():
        contract = server_contracts.get(name)
        if contract is None:
            continue
        live_schema = live_tool.get("input_schema")
        if _schema_signature(live_schema) != _schema_signature(contract.input_schema):
            raise ToolContractError("MCP input schema differs from host contract", tool=name)

    return [
        contract.anthropic_tool()
        for name, contract in server_contracts.items()
        if name in live_by_name
    ]


def _schema_signature(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return schema
    ignored = {"title", "description", "default"}
    return {
        key: _schema_signature(value) for key, value in sorted(schema.items()) if key not in ignored
    }


__all__ = [
    "TOOL_CONTRACTS",
    "ToolContract",
    "ToolContractError",
    "canonical_tools_for_server",
    "contract_for_tool",
    "tools_for_intent",
    "validate_live_inventory",
]
