"""Legacy workflows over the fabricated proof-of-concept catalog.

This module preserves existing behavior while the RAG-backed documentation and
SQL workflows are integrated. It is a deletion boundary, not an abstraction
for real schemas. Nothing in ``documentation.py``, ``sql.py``, or
``retrieval.py`` should import from this module.
"""

from __future__ import annotations

import json
from typing import Any

from agent_host.budget import ExecutionBudget
from agent_host.config import Settings
from agent_host.mcp_bridge import MCPToolBridge
from agent_host.responses import screened_answer_response
from agent_host.schemas import AskResponse
from agent_host.tool_execution import call_workflow_tool
from agent_host.trace_logger import TraceLogger
from mcp_servers.intent import IntentResult
from retrieval.client import RetrievalResult, retrieve_documentation


async def run_legacy_sql_workflow(
    question: str,
    classification: IntentResult,
    settings: Settings,
    trace: TraceLogger,
    *,
    budget: ExecutionBudget,
    bridge_factory: type[MCPToolBridge],
) -> AskResponse:
    """Preserve the mock catalog → generation → validation SQL chain."""
    used_tools: list[str] = []
    trace.record("sql.workflow.started", intent=classification.model_dump())

    retrieval = await retrieve_documentation(
        question,
        settings,
        trace,
        budget=budget,
        top_k=budget.max_retrieved_chunks,
    )
    used_tools.append("search_docs")
    used_tools.extend("get_doc_chunk" for _ in retrieval.chunks)
    if not retrieval.chunks:
        return sql_workflow_response(
            "I couldn't find approved documentation context for that SQL request, "
            "so I stopped before generating SQL.",
            used_tools,
            trace,
            classification,
        )

    schema_context = sql_schema_context_from_retrieval(retrieval)

    async with bridge_factory(
        settings.sql_generation_mcp_url,
        auth_token=getattr(settings, "mcp_auth_token", None),
    ) as generation_mcp:
        generated = await call_workflow_tool(
            generation_mcp,
            "generate_sql",
            {"question": question, "schema_context": schema_context},
            trace,
            used_tools,
            budget=budget,
            server="sql_generation",
        )

    if not isinstance(generated, dict):
        trace.record("sql.generation.failed", error="invalid_result")
        return sql_workflow_response(
            "I couldn't generate SQL in a structured format, so I stopped.",
            used_tools,
            trace,
            classification,
        )
    if generated.get("refused") or not generated.get("sql"):
        trace.record("sql.generation.refused", result=generated)
        return sql_workflow_response(
            "I can't generate SQL for that request because the SQL generation "
            f"node refused it: {generated.get('reason') or 'unsafe_or_unsupported_request'}.",
            used_tools,
            trace,
            classification,
        )

    async with bridge_factory(
        settings.sql_validation_mcp_url,
        auth_token=getattr(settings, "mcp_auth_token", None),
    ) as validation_mcp:
        validation = await call_workflow_tool(
            validation_mcp,
            "validate_sql",
            {"sql": generated["sql"], "tables": generated.get("tables", [])},
            trace,
            used_tools,
            budget=budget,
            server="sql_validation",
        )

    if not isinstance(validation, dict):
        trace.record("sql.validation.failed", error="invalid_result")
        return sql_workflow_response(
            "I couldn't validate the generated SQL, so I stopped before returning it.",
            used_tools,
            trace,
            classification,
        )
    if not validation.get("allowed"):
        trace.record("sql.validation.blocked", result=validation)
        return sql_workflow_response(
            "I generated a SQL candidate, but the SQL validation node blocked it: "
            f"{validation.get('reason') or 'failed_validation'}.",
            used_tools,
            trace,
            classification,
        )

    sql = validation.get("normalized_sql")
    if not isinstance(sql, str) or not sql.strip():
        trace.record("sql.validation.failed", error="missing_normalized_sql")
        return sql_workflow_response(
            "The SQL validation result was incomplete, so I stopped before returning SQL.",
            used_tools,
            trace,
            classification,
        )
    disclosure_status = validation.get("disclosure_status", "not_evaluated")
    answer = (
        "Here is structurally validated mock SQL for that aggregate request. "
        "It was not executed, authorized, cost-checked, or evaluated for minimum "
        f"cell-size disclosure (status: {disclosure_status}):\n\n"
        f"```sql\n{sql}\n```"
    )
    trace.record(
        "sql.workflow.completed",
        sql=sql,
        disclosure_status=disclosure_status,
        used_tools=used_tools,
    )
    return sql_workflow_response(
        answer,
        used_tools,
        trace,
        classification,
        disclosure_status=str(disclosure_status),
    )


def sql_schema_context_from_retrieval(retrieval: RetrievalResult) -> str:
    """Build compact approved schema context for the SQL generation node."""
    chunks = []
    for chunk in retrieval.chunks:
        chunks.append(
            {
                "chunk_id": chunk.chunk_id,
                "source_path": chunk.source_path,
                "heading_path": chunk.heading_path,
                "rank": chunk.rank,
                "text": chunk.text[:1_800],
            }
        )
    return json.dumps(
        {
            "source": "rag_documentation",
            "index_version": retrieval.index_version,
            "query": retrieval.query,
            "chunks": chunks,
        }
    )


async def fetch_candidate_schemas(
    catalog_mcp: MCPToolBridge,
    search_result: Any,
    trace: TraceLogger,
    used_tools: list[str],
    *,
    budget: ExecutionBudget,
) -> list[Any]:
    all_table_names = candidate_table_names(search_result)
    if len(all_table_names) > budget.max_candidate_schemas:
        trace.record(
            "budget.candidate_schemas_limited",
            requested=len(all_table_names),
            allowed=budget.max_candidate_schemas,
        )
    table_names = all_table_names[: budget.max_candidate_schemas]
    schemas = []
    for table_name in table_names:
        schemas.append(
            await call_workflow_tool(
                catalog_mcp,
                "get_table_schema",
                {"table_name": table_name},
                trace,
                used_tools,
                budget=budget,
                server="catalog",
            )
        )
    return schemas


def candidate_table_names(search_result: Any) -> list[str]:
    if not isinstance(search_result, dict):
        return []
    candidates = search_result.get("candidates")
    if not isinstance(candidates, list):
        return []

    table_names: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        table_name = candidate.get("table_name")
        if isinstance(table_name, str) and table_name not in table_names:
            table_names.append(table_name)
    return table_names


def sql_workflow_response(
    answer: str,
    used_tools: list[str],
    trace: TraceLogger,
    classification: IntentResult,
    disclosure_status: str | None = None,
) -> AskResponse:
    response = screened_answer_response(
        answer,
        trace,
        used_tools,
        intent=classification.intent,
        intent_confidence=classification.confidence,
    )
    if disclosure_status is not None:
        response.disclosure_status = disclosure_status
    return response
