from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from agent_host.budget import ExecutionBudget
from agent_host.tools import execute_retrieval_tool, tools_for_intent
from retrieval.index_contract import INDEX_CHUNKER_VERSION, INDEX_SCHEMA_VERSION


def _index(tmp_path):
    path = tmp_path / "index.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE docs (
            doc_id TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            title TEXT NOT NULL
        );
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            heading_path TEXT,
            category TEXT,
            text TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            chunk_id, source_path, title, category, heading_path, text,
            generated_queries
        );
        CREATE TABLE index_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT INTO index_metadata VALUES (?, ?)",
        [
            ("schema_version", INDEX_SCHEMA_VERSION),
            ("chunker_version", INDEX_CHUNKER_VERSION),
        ],
    )
    conn.execute(
        "INSERT INTO docs VALUES (?, ?, ?)",
        ("appointments", "APP_APPOINTMENT.html", "APP_APPOINTMENT - Clarity Dictionary"),
    )
    rows = [
        (
            "table-chunk",
            "appointments",
            0,
            "APP_APPOINTMENT > Description",
            "metadata",
            "Appointment records and scheduling metadata.",
        ),
        (
            "status-chunk",
            "appointments",
            1,
            "APP_APPOINTMENT > Column Information > APPT_STATUS",
            "column_info",
            "APPT_STATUS stores appointment status codes.",
        ),
    ]
    conn.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.executemany(
        "INSERT INTO chunks_fts VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                row[0],
                "APP_APPOINTMENT.html",
                "APP_APPOINTMENT - Clarity Dictionary",
                row[4],
                row[3],
                row[5],
                "",
            )
            for row in rows
        ],
    )
    conn.commit()
    conn.close()
    return path


def test_registry_scopes_tools_by_intent() -> None:
    table_tools = {tool["name"] for tool in tools_for_intent("table_discovery")}
    schema_tools = {tool["name"] for tool in tools_for_intent("schema_lookup")}
    sql_tools = {tool["name"] for tool in tools_for_intent("safe_sql_generation")}

    assert table_tools == {"find_table_doc", "search_columns"}
    assert "get_doc_section" in schema_tools
    assert sql_tools == {"find_table_doc", "get_doc_section", "search_columns"}
    assert tools_for_intent("general_question") == []


def test_execution_rechecks_authority(tmp_path) -> None:
    with pytest.raises(PermissionError):
        execute_retrieval_tool(
            "table_discovery",
            "get_doc_section",
            {"document_name": "APP_APPOINTMENT", "section_name": "Column Information"},
            db_path=_index(tmp_path),
            budget=ExecutionBudget(),
        )


def test_execution_validates_arguments_and_returns_bounded_chunks(tmp_path) -> None:
    path = _index(tmp_path)
    budget = ExecutionBudget(max_tool_calls=1)

    result = execute_retrieval_tool(
        "schema_lookup",
        "find_table_doc",
        {"table_name": "APP_APPOINTMENT", "limit": 1},
        db_path=path,
        budget=budget,
    )

    assert [chunk["chunk_id"] for chunk in result["chunks"]] == ["table-chunk"]
    assert budget.tool_calls == 1

    with pytest.raises(ValidationError):
        execute_retrieval_tool(
            "schema_lookup",
            "find_table_doc",
            {"table_name": "APP_APPOINTMENT", "limit": 100},
            db_path=path,
            budget=ExecutionBudget(),
        )
