from __future__ import annotations

from fastmcp import FastMCP
from pydantic import BaseModel, Field


mcp = FastMCP("data_catalog")


class SearchTablesArgs(BaseModel):
    question: str = Field(min_length=1)


class GetTableSchemaArgs(BaseModel):
    table_name: str = Field(min_length=1)


class SearchDocsArgs(BaseModel):
    query: str = Field(min_length=1)


class GetTableInfoArgs(BaseModel):
    table_name: str = Field(min_length=1)


TABLES: dict[str, dict[str, object]] = {
    "encounters": {
        "description": "One row per patient visit or encounter.",
        "columns": [
            {
                "name": "encounter_id",
                "type": "string",
                "description": "Internal visit identifier.",
                "safety_label": "identifier",
            },
            {
                "name": "patient_id",
                "type": "string",
                "description": "Internal patient identifier.",
                "safety_label": "identifier",
            },
            {
                "name": "encounter_date",
                "type": "date",
                "description": "Date when the visit occurred.",
                "safety_label": "safe_aggregate",
            },
            {
                "name": "department",
                "type": "string",
                "description": "Department where the visit occurred.",
                "safety_label": "safe_aggregate",
            },
        ],
    },
    "patients": {
        "description": "One row per patient in the dummy catalog.",
        "columns": [
            {
                "name": "patient_id",
                "type": "string",
                "description": "Internal patient identifier.",
                "safety_label": "identifier",
            },
            {
                "name": "birth_year",
                "type": "integer",
                "description": "Patient birth year, not full date of birth.",
                "safety_label": "safe_aggregate",
            },
            {
                "name": "zip3",
                "type": "string",
                "description": "First three digits of patient ZIP code.",
                "safety_label": "sensitive",
            },
        ],
    },
    "appointments": {
        "description": "Scheduled appointment records.",
        "columns": [
            {
                "name": "appointment_id",
                "type": "string",
                "description": "Internal appointment identifier.",
                "safety_label": "identifier",
            },
            {
                "name": "appointment_date",
                "type": "date",
                "description": "Scheduled appointment date.",
                "safety_label": "safe_aggregate",
            },
            {
                "name": "status",
                "type": "string",
                "description": "Scheduled, completed, cancelled, or no-show.",
                "safety_label": "safe_aggregate",
            },
        ],
    },
}


DOCS: list[dict[str, str]] = [
    {
        "name": "encounters",
        "description": (
            "Contains admission, discharge, department, and encounter date "
            "information for dummy hospital visits."
        ),
    },
    {
        "name": "diagnoses",
        "description": (
            "Contains diagnosis codes and diagnosis descriptions for dummy "
            "clinical metadata exploration."
        ),
    },
    {
        "name": "departments",
        "description": (
            "Contains department names and department metadata for dummy "
            "operational analytics."
        ),
    },
]


TABLE_INFO: dict[str, dict[str, str]] = {
    "encounters": {
        "table": "encounters",
        "primary_key": "encounter_id",
        "description": (
            "Contains admission, discharge, department, and encounter date "
            "information."
        ),
    },
    "diagnoses": {
        "table": "diagnoses",
        "primary_key": "diagnosis_id",
        "description": "Contains diagnosis codes and diagnosis descriptions.",
    },
    "departments": {
        "table": "departments",
        "primary_key": "department_id",
        "description": "Contains department names and department metadata.",
    },
}


@mcp.tool
def search_docs(query: str) -> list[dict[str, str]]:
    """Search tiny hospital documentation for relevant dummy tables."""
    args = SearchDocsArgs(query=query.strip())
    q = args.query.lower()
    return [
        doc
        for doc in DOCS
        if q in f"{doc['name']} {doc['description']}".lower()
    ]


@mcp.tool
def get_table_info(table_name: str) -> dict[str, str]:
    """Return metadata about a mock hospital table."""
    args = GetTableInfoArgs(table_name=table_name.strip())
    table = TABLE_INFO.get(args.table_name.lower())
    if table is None:
        return {
            "error": f"Unknown table: {args.table_name}",
        }
    return table


@mcp.tool
def search_tables(question: str) -> dict[str, object]:
    """
    Find candidate tables for a natural-language data question.

    Use this first when the user asks what data could answer a question,
    which tables might be relevant, or where to start exploring.
    This returns dummy catalog data, not real patient or operational data.
    """
    args = SearchTablesArgs(question=question.strip())
    return {
        "question": args.question,
        "candidates": [
            {
                "table_name": table_name,
                "description": table["description"],
                "why_relevant": _why_relevant(args.question, table_name),
            }
            for table_name, table in TABLES.items()
        ],
        "source": "dummy_mcp_data_catalog",
        "is_dummy": True,
    }


@mcp.tool
def get_table_schema(table_name: str) -> dict[str, object]:
    """
    Get the dummy schema for one candidate table.

    Use this after search_tables when you need column names, meanings, or
    safety labels before explaining how a data question could be answered.
    """
    args = GetTableSchemaArgs(table_name=table_name.strip())
    table = TABLES.get(args.table_name)
    if table is None:
        return {
            "table_name": args.table_name,
            "error": "unknown_table",
            "known_tables": sorted(TABLES),
            "source": "dummy_mcp_data_catalog",
            "is_dummy": True,
        }

    return {
        "table_name": args.table_name,
        "description": table["description"],
        "columns": table["columns"],
        "source": "dummy_mcp_data_catalog",
        "is_dummy": True,
    }


def _why_relevant(question: str, table_name: str) -> str:
    question_lower = question.lower()
    if table_name == "encounters" and any(
        word in question_lower for word in ("visit", "encounter", "last month")
    ):
        return "Contains visit dates and departments for encounter-level analysis."
    if table_name == "appointments" and "appointment" in question_lower:
        return "Contains scheduled appointment dates and statuses."
    if table_name == "patients" and any(
        word in question_lower for word in ("patient", "age", "birth")
    ):
        return "Contains patient-level demographic fields."
    return "Potentially relevant catalog entry for initial exploration."


def main() -> None:
    mcp.run(transport="http", host="localhost", port=8000, path="/mcp")


if __name__ == "__main__":
    main()
