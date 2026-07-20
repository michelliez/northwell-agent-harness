"""Legacy workflows over the fabricated proof-of-concept catalog.

This module preserves existing behavior while the RAG-backed documentation and
SQL workflows are integrated. It is a deletion boundary, not an abstraction
for real schemas. Nothing in ``documentation.py``, ``sql.py``, or
``retrieval.py`` should import from this module.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from anthropic import Anthropic

from harness_spike.agent_host.budget import ExecutionBudget
from harness_spike.agent_host.mcp_bridge import MCPToolBridge
from harness_spike.agent_host.model_call import tool_names
from harness_spike.agent_host.model_runtime import run_agent_loop
from harness_spike.agent_host.responses import (
    content_blocked_response,
    screened_answer_response,
    tool_contract_error_response,
)
from harness_spike.agent_host.schemas import AskResponse
from harness_spike.agent_host.tool_execution import (
    call_workflow_tool,
    discover_tools,
)
from harness_spike.agent_host.tool_registry import (
    ToolContractError,
    tools_for_intent,
)
from harness_spike.agent_host.trace_logger import TraceLogger
from harness_spike.config import Settings
from harness_spike.mcp_servers.intent import IntentResult
from harness_spike.policy.screen import ContentScreenBlocked

BridgeFactory = Callable[..., MCPToolBridge]
ModelClientFactory = Callable[[Settings], Anthropic]


async def run_legacy_catalog_workflow(
    question: str,
    classification: IntentResult,
    settings: Settings,
    trace: TraceLogger,
    *,
    budget: ExecutionBudget,
    bridge_factory: BridgeFactory,
    model_client_factory: ModelClientFactory,
    system: str,
) -> AskResponse:
    """Preserve the model-driven workflow over fabricated catalog metadata."""
    async with bridge_factory(
        settings.mcp_server_url,
        auth_token=getattr(settings, "mcp_auth_token", None),
    ) as mcp:
        allowed_tools = tools_for_intent(classification.intent)
        try:
            discovered_tools = await discover_tools(
                mcp,
                trace,
                server="catalog",
                mcp_url=settings.mcp_server_url,
                required_tools=allowed_tools,
                log_raw_prompts=settings.log_raw_prompts,
            )
        except ContentScreenBlocked as exc:
            return content_blocked_response(trace, exc)
        except ToolContractError as exc:
            return tool_contract_error_response(trace, exc)
        tools = [tool for tool in discovered_tools if tool.get("name") in allowed_tools]
        trace.record(
            "mcp.tools.scoped",
            intent=classification.intent,
            allowed_tools=sorted(allowed_tools),
            tools=tool_names(tools),
        )
        response = await run_agent_loop(
            question=question,
            client=model_client_factory(settings),
            model=settings.require_claude_model(),
            tools=tools,
            mcp=mcp,
            settings=settings,
            trace=trace,
            system=system,
            allowed_tools=allowed_tools,
            budget=budget,
            server="catalog",
        )

    return response.model_copy(
        update={
            "intent": classification.intent,
            "intent_confidence": classification.confidence,
        }
    )


async def run_legacy_sql_workflow(
    question: str,
    classification: IntentResult,
    settings: Settings,
    trace: TraceLogger,
    *,
    budget: ExecutionBudget,
    bridge_factory: BridgeFactory,
) -> AskResponse:
    """Preserve the mock catalog → generation → validation SQL chain."""
    used_tools: list[str] = []
    trace.record("sql.workflow.started", intent=classification.model_dump())

    async with bridge_factory(
        settings.mcp_server_url,
        auth_token=getattr(settings, "mcp_auth_token", None),
    ) as catalog_mcp:
        search_result = await call_workflow_tool(
            catalog_mcp,
            "search_tables",
            {"question": question},
            trace,
            used_tools,
            budget=budget,
            server="catalog",
        )
        schemas = await fetch_candidate_schemas(
            catalog_mcp,
            search_result,
            trace,
            used_tools,
            budget=budget,
        )

    schema_context = json.dumps({"search_tables": search_result, "schemas": schemas})

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
