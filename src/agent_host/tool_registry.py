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


class IndexedDocumentationChunk(_PermissiveModel):
    chunk_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    title: str
    heading_path: str | None = None
    text: str = Field(min_length=1)


class RankedDocumentationChunk(IndexedDocumentationChunk):
    rank: int = Field(ge=1)
    score: float | None = None


class DocumentationContextResult(_PermissiveModel):
    query: str
    chunks: list[RankedDocumentationChunk] = Field(default_factory=list)
    index_version: str


class TableDocumentMatch(_PermissiveModel):
    doc_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    title: str


class TableDocumentSearchResult(_PermissiveModel):
    query: str
    matches: list[TableDocumentMatch] = Field(default_factory=list)
    index_version: str


class DocumentSectionResult(_PermissiveModel):
    doc_query: str
    section_query: str
    chunks: list[IndexedDocumentationChunk] = Field(default_factory=list)
    index_version: str


class ColumnSearchMatch(_PermissiveModel):
    chunk_id: str = Field(min_length=1)
    title: str
    heading_path: str | None = None
    score: float | None = None
    preview: str


class ColumnSearchResult(_PermissiveModel):
    query: str
    matches: list[ColumnSearchMatch] = Field(default_factory=list)
    index_version: str


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
    if allowed == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise ToolContractError(f"{field_name} must be an integer", tool=tool_name)
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
    if isinstance(value, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if min_length is not None and len(value) < min_length:
            raise ToolContractError(
                f"{field_name} must contain at least {min_length} characters",
                tool=tool_name,
            )
        if max_length is not None and len(value) > max_length:
            raise ToolContractError(
                f"{field_name} must contain at most {max_length} characters",
                tool=tool_name,
            )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            raise ToolContractError(
                f"{field_name} must be at least {minimum}", tool=tool_name
            )
        if maximum is not None and value > maximum:
            raise ToolContractError(
                f"{field_name} must be at most {maximum}", tool=tool_name
            )


def _model_result(model: type[BaseModel], result: Any) -> Any:
    return model.model_validate(result).model_dump()


TOOL_CONTRACTS: dict[str, ToolContract] = {
    "retrieve_documentation_context": ToolContract(
        name="retrieve_documentation_context",
        server="rag",
        description=(
            "Search approved indexed documentation and return bounded, fully cited chunks."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 4_000},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 25},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        routes=frozenset(
            {
                "documentation_lookup",
                "table_discovery",
                "schema_lookup",
                "aggregate_definition",
            }
        ),
        result_validator=lambda result: _model_result(DocumentationContextResult, result),
    ),
    "find_table_doc": ToolContract(
        name="find_table_doc",
        server="rag",
        description="Find indexed documentation pages for an explicit table name.",
        input_schema={
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "minLength": 1, "maxLength": 256},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 25},
            },
            "required": ["table_name"],
            "additionalProperties": False,
        },
        routes=frozenset({"schema_lookup"}),
        result_validator=lambda result: _model_result(TableDocumentSearchResult, result),
    ),
    "get_doc_section": ToolContract(
        name="get_doc_section",
        server="rag",
        description="Fetch bounded chunks from a named section of an indexed table document.",
        input_schema={
            "type": "object",
            "properties": {
                "doc_query": {"type": "string", "minLength": 1, "maxLength": 256},
                "section_query": {"type": "string", "minLength": 1, "maxLength": 256},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 25},
            },
            "required": ["doc_query", "section_query"],
            "additionalProperties": False,
        },
        routes=frozenset({"schema_lookup", "aggregate_definition"}),
        result_validator=lambda result: _model_result(DocumentSectionResult, result),
    ),
    "search_columns": ToolContract(
        name="search_columns",
        server="rag",
        description="Search real indexed column-information chunks.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 4_000},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 25},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        routes=frozenset({"schema_lookup", "aggregate_definition"}),
        result_validator=lambda result: _model_result(ColumnSearchResult, result),
    ),
    "search_tables": ToolContract(
        name="search_tables",
        server="catalog",
        description=(
            "Find candidate tables in the deprecated compatibility catalog."
        ),
        input_schema={
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        },
        routes=frozenset({"safe_sql_generation"}),
        result_validator=lambda result: _model_result(SearchTablesResult, result),
    ),
    "get_table_schema": ToolContract(
        name="get_table_schema",
        server="catalog",
        description="Get one schema from the deprecated compatibility catalog.",
        input_schema={
            "type": "object",
            "properties": {"table_name": {"type": "string"}},
            "required": ["table_name"],
            "additionalProperties": False,
        },
        routes=frozenset({"safe_sql_generation"}),
        result_validator=lambda result: _model_result(TableSchemaResult, result),
    ),
    "generate_sql": ToolContract(
        name="generate_sql",
        server="sql_generation",
        description="Generate structured aggregate SQL from supplied schema evidence.",
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
        description="Structurally validate one aggregate SQL query.",
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
