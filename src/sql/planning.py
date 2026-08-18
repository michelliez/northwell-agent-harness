"""Claude plan proposal and deterministic QueryPlanAST authorization."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Protocol

from anthropic import Anthropic
from anthropic.types import ToolUseBlock

from agent_host.budget import ExecutionBudget
from agent_host.config import AppConfig
from agent_host.model_usage import record_anthropic_usage
from agent_host.trace_logger import TraceLogger
from sql.models import (
    ApprovedQueryPlan,
    CatalogRef,
    PermissionScope,
    PlanValidationResult,
    PlanViolation,
    QueryPlanAST,
    SchemaColumn,
    SchemaSnapshot,
)

PLAN_SYSTEM_PROMPT = """
Create a structured aggregate-query plan, not SQL. Use only the supplied
permission scope and cited schema evidence. Copy the user's analytical goal
verbatim into objective; if the current request refers to a prior conversation
turn (e.g. "that query", "the same but for X"), resolve the reference using
the conversation history and copy the resolved goal. Never introduce tables,
columns, filters, joins, metrics, or output fields not requested or supported
by the scope. All predicates must use named parameters; never put literal
values or SQL fragments in the plan.
Identifiers may only be counted or joined to identifiers. Unknown and sensitive
columns may not be used. Row-level output is forbidden.
For per-day/week/month/quarter/year questions, use time_buckets on a temporal
safe_aggregate column; its alias is an output field. A time-bucketed column
must not also appear in groupings; groupings are only for additional
categorical columns. expected_output must list exactly every aggregation
alias, every grouping column name, and every time_bucket alias — nothing
else. For top-N or ranked requests, use order_by (one output alias) and
limit. Range predicates use BETWEEN with two parameters; there is no IN
operator.
""".strip()

_PLAN_TOOL: dict[str, Any] = {
    "name": "emit_query_plan",
    "description": "Return a structured query plan without SQL.",
    "input_schema": QueryPlanAST.model_json_schema(),
}


class MessagesClient(Protocol):
    class Messages(Protocol):
        def create(self, **kwargs: Any) -> Any: ...

    messages: Messages


def permission_scope_from_snapshot(snapshot: SchemaSnapshot) -> PermissionScope:
    """Create the initial host-owned aggregate-only scope for this MVP."""
    return PermissionScope(schema_snapshot=snapshot)


def propose_query_plan(
    question: str,
    scope: PermissionScope,
    citations: list[str],
    config: AppConfig,
    budget: ExecutionBudget,
    *,
    client: MessagesClient | None = None,
    feedback: str | None = None,
    history: list[dict] | None = None,
    trace: TraceLogger | None = None,
) -> QueryPlanAST:
    """Ask Claude for one schema-constrained plan through a forced tool call.

    ``feedback`` carries host-composed rejection details from a previous
    attempt (see ``repair_feedback``); the corrected plan still goes through
    the same deterministic authorization — feedback never grants anything.
    ``history`` is the prior conversation as a messages array so the planner
    can resolve follow-up references such as "that query" naturally.
    """
    content = (
        f"User request:\n{question}\n\n"
        f"Permission scope:\n{scope.model_dump_json()}\n\n"
        f"Available citation IDs:\n{json.dumps(citations)}"
    )
    if feedback:
        content += (
            "\n\nYour previous plan was rejected by the deterministic "
            "authorizer. Emit a corrected plan that resolves every violation "
            f"below without changing the user's request.\n{feedback}"
        )
    messages = [*(history or []), {"role": "user", "content": content}]
    budget.reserve_model_call(messages)
    active_client = client or Anthropic(
        api_key=config.require_api_key(),
        base_url=config.require_base_url() if config.anthropic_base_url else None,
        default_headers=config.anthropic_custom_headers,
    )
    response = active_client.messages.create(
        model=config.require_model(),
        max_tokens=budget.model_max_tokens,
        system=PLAN_SYSTEM_PROMPT,
        messages=messages,  # type: ignore[arg-type]
        tools=[_PLAN_TOOL],  # type: ignore[arg-type]
        tool_choice={"type": "tool", "name": "emit_query_plan"},
        timeout=budget.model_call_timeout_seconds,
    )
    if trace is not None:
        record_anthropic_usage(
            response,
            trace=trace,
            artifact_path=config.artifact_path,
            operation="query_planning",
            model=config.require_model(),
        )
    tool_uses = [
        block
        for block in response.content
        if isinstance(block, ToolUseBlock) and block.name == "emit_query_plan"
    ]
    if len(tool_uses) != 1:
        raise RuntimeError("query planner did not return exactly one plan")
    return QueryPlanAST.model_validate(tool_uses[0].input)


def repair_feedback(proposed: QueryPlanAST, validation: PlanValidationResult) -> str:
    """Serialize a rejection into deterministic, host-owned repair guidance.

    Only host-produced material goes in: the rejected plan the model itself
    emitted and the authorizer's violation codes, messages, and evidence.
    Retrieved documentation content is never echoed here.
    """
    lines = []
    for violation in validation.violations:
        line = f"- {violation.code}: {violation.message}"
        value = violation.evidence.get("value")
        if value is not None:
            line += f" Evidence: {json.dumps(value, sort_keys=True, default=str)}"
        lines.append(line)
    return f"Rejected plan:\n{proposed.model_dump_json()}\n\nViolations:\n" + "\n".join(lines)


# The only identifier shape the compiler may ever emit. Snapshot names come
# from retrieved documentation, which is untrusted: a poisoned table or column
# name would otherwise flow through an approved plan into SQL text (sqlglot
# tokenizes raw names, so a quote-bearing name crashes or worse). Aliases are
# already regex-bound by the plan model; this closes the same door for names.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,255}$")


def validate_query_plan(
    question: str,
    proposed: QueryPlanAST,
    scope: PermissionScope,
    available_citations: set[str],
) -> PlanValidationResult:
    """Authorize a proposed plan using only deterministic structural checks."""
    violations: list[PlanViolation] = []
    snapshot = scope.schema_snapshot
    tables = snapshot.tables_by_name

    if _normalized(proposed.objective) != _normalized(question):
        violations.append(
            _violation(
                "objective_mismatch",
                "Plan objective differs from prompt.",
                {"proposed": proposed.objective, "expected": question},
            )
        )

    malformed = {
        name
        for name in [
            *proposed.tables,
            *(ref.table for ref in _all_references(proposed)),
            *(ref.column for ref in _all_references(proposed)),
        ]
        if not _IDENTIFIER_RE.fullmatch(name)
    }
    if malformed:
        violations.append(
            _violation(
                "malformed_identifier",
                "Table and column names must be plain alphanumeric identifiers.",
                malformed,
            )
        )

    restricted_names = {
        column.name.casefold()
        for table in snapshot.tables
        for column in table.columns
        if column.safety != "safe_aggregate"
    }
    shadowing = {
        alias
        for alias in [
            *(item.alias for item in proposed.aggregations),
            *(item.alias for item in proposed.time_buckets),
        ]
        if alias.casefold() in restricted_names
    }
    if shadowing:
        violations.append(
            _violation(
                "alias_shadows_restricted_column",
                "Output aliases may not reuse a restricted column's name.",
                shadowing,
            )
        )

    proposed_tables = {name.casefold() for name in proposed.tables}
    if len(proposed_tables) > scope.max_tables:
        violations.append(_violation("too_many_tables", "Plan exceeds table complexity limit."))
    unknown_tables = proposed_tables - snapshot.known_table_names
    if unknown_tables:
        violations.append(
            _violation("table_out_of_scope", "Plan references an unapproved table.", unknown_tables)
        )
    if len(proposed.joins) > scope.max_joins:
        violations.append(_violation("too_many_joins", "Plan exceeds join complexity limit."))
    if scope.aggregate_only and proposed.requires_row_level_access:
        violations.append(
            _violation("row_level_not_allowed", "Permission scope is aggregate-only.")
        )

    references = _all_references(proposed)
    for reference in references:
        if reference.table.casefold() not in proposed_tables:
            violations.append(
                _violation(
                    "undeclared_plan_table",
                    "A column reference uses a table absent from plan.tables.",
                    reference.table,
                )
            )
        column = _resolve_column(reference, tables)
        if column is None:
            violations.append(
                _violation(
                    "column_out_of_scope",
                    "Plan references an unapproved column.",
                    f"{reference.table}.{reference.column}",
                )
            )

    projected_refs = [*proposed.columns, *proposed.groupings]
    for reference in projected_refs:
        column = _resolve_column(reference, tables)
        if column is not None and column.safety in {"identifier", "sensitive", "unknown"}:
            violations.append(
                _violation(
                    "unsafe_projection",
                    "Restricted columns cannot be projected or grouped.",
                    f"{reference.table}.{reference.column}",
                )
            )

    _TEMPORAL_TYPES = {"DATE", "DATETIME", "TIMESTAMP"}
    for bucket in proposed.time_buckets:
        column = _resolve_column(bucket.column, tables)
        if column is None:
            continue  # column_out_of_scope already recorded above
        if column.safety != "safe_aggregate":
            violations.append(
                _violation(
                    "time_bucket_unsafe_column",
                    "Time buckets require a column classified safe_aggregate.",
                    f"{bucket.column.table}.{bucket.column.column}",
                )
            )
        data_type = (column.data_type or "").upper().split("(", 1)[0].strip()
        if data_type not in _TEMPORAL_TYPES:
            violations.append(
                _violation(
                    "time_bucket_not_temporal",
                    "Time buckets require an evidenced DATE, DATETIME, or TIMESTAMP column.",
                    f"{bucket.column.table}.{bucket.column.column}",
                )
            )

    output_aliases = [
        *(aggregation.alias for aggregation in proposed.aggregations),
        *(bucket.alias for bucket in proposed.time_buckets),
    ]
    if len({alias.casefold() for alias in output_aliases}) != len(output_aliases):
        violations.append(_violation("duplicate_output_alias", "Output aliases must be unique."))

    if proposed.order_by is not None:
        known_outputs = {alias.casefold() for alias in output_aliases} | {
            group.column.casefold() for group in proposed.groupings
        }
        if proposed.order_by.alias.casefold() not in known_outputs:
            violations.append(
                _violation(
                    "order_by_unknown_alias",
                    "ORDER BY must reference an output alias or grouping column.",
                    proposed.order_by.alias,
                )
            )

    for aggregation in proposed.aggregations:
        if aggregation.column is None:
            continue
        column = _resolve_column(aggregation.column, tables)
        if column is None:
            continue
        if column.safety in {"sensitive", "unknown"}:
            violations.append(
                _violation("unsafe_aggregation", "Restricted column cannot be aggregated.")
            )
        if column.safety == "identifier" and aggregation.function not in {
            "COUNT",
            "COUNT_DISTINCT",
        }:
            violations.append(_violation("identifier_usage", "Identifier may only be counted."))

    for planned_filter in [*proposed.filters, *proposed.time_constraints]:
        column = _resolve_column(planned_filter.column, tables)
        if column is not None and column.safety != "safe_aggregate":
            violations.append(
                _violation(
                    "unsafe_filter",
                    "Filters require a column classified safe_aggregate.",
                    f"{planned_filter.column.table}.{planned_filter.column.column}",
                )
            )

    parameter_names = [parameter.name for parameter in proposed.parameters]
    if len({name.casefold() for name in parameter_names}) != len(parameter_names):
        violations.append(_violation("duplicate_parameter", "Parameter names must be unique."))
    referenced_parameter_names = {
        name.casefold()
        for planned_filter in [*proposed.filters, *proposed.time_constraints]
        for name in planned_filter.parameter_names
    }
    declared_parameter_names = {name.casefold() for name in parameter_names}
    if referenced_parameter_names - declared_parameter_names:
        violations.append(
            _violation(
                "undeclared_parameter",
                "Every filter parameter must be declared.",
                referenced_parameter_names - declared_parameter_names,
            )
        )
    if declared_parameter_names - referenced_parameter_names:
        violations.append(
            _violation(
                "unused_parameter",
                "Plans cannot carry unused parameter values.",
                declared_parameter_names - referenced_parameter_names,
            )
        )
    parameters_by_name = {parameter.name.casefold(): parameter for parameter in proposed.parameters}
    for planned_filter in [*proposed.filters, *proposed.time_constraints]:
        column = _resolve_column(planned_filter.column, tables)
        if column is None or column.data_type is None:
            continue
        expected_type = _parameter_type_for_column(column.data_type)
        for name in planned_filter.parameter_names:
            parameter = parameters_by_name.get(name.casefold())
            if (
                parameter is not None
                and expected_type is not None
                and parameter.type != expected_type
            ):
                violations.append(
                    _violation(
                        "parameter_type_mismatch",
                        "Parameter type is incompatible with its filter column.",
                        {
                            "parameter": parameter.name,
                            "expected": expected_type,
                            "actual": parameter.type,
                        },
                    )
                )

    for join in proposed.joins:
        left = _resolve_column(join.left, tables)
        right = _resolve_column(join.right, tables)
        if (
            left is not None
            and right is not None
            and (left.safety != "identifier" or right.safety != "identifier")
        ):
            violations.append(
                _violation("unsafe_join", "Joins must compare catalog-backed identifiers.")
            )

    missing_citations = set(proposed.citations) - available_citations
    if missing_citations:
        violations.append(
            _violation(
                "citation_out_of_scope",
                "Plan cites evidence that was not retrieved.",
                missing_citations,
            )
        )

    expected_outputs = (
        {aggregation.alias.casefold() for aggregation in proposed.aggregations}
        | {group.column.casefold() for group in proposed.groupings}
        | {bucket.alias.casefold() for bucket in proposed.time_buckets}
    )
    declared_outputs = {value.casefold() for value in proposed.expected_output}
    if declared_outputs != expected_outputs:
        violations.append(
            _violation(
                "output_shape_mismatch",
                "Expected output must equal grouping names and aggregation aliases.",
                {
                    "declared": sorted(declared_outputs),
                    "required": sorted(expected_outputs),
                },
            )
        )

    if violations:
        return PlanValidationResult(allowed=False, violations=violations)

    plan_hash = _hash_model(proposed)
    scope_hash = _hash_model(scope)
    return PlanValidationResult(
        allowed=True,
        approved_plan=ApprovedQueryPlan(
            plan=proposed,
            permission_scope=scope,
            plan_hash=plan_hash,
            scope_hash=scope_hash,
        ),
    )


def _all_references(plan: QueryPlanAST) -> list[CatalogRef]:
    references = [*plan.columns, *plan.groupings]
    references.extend(item.column for item in plan.filters)
    references.extend(item.column for item in plan.time_constraints)
    references.extend(item.column for item in plan.aggregations if item.column is not None)
    references.extend(item.column for item in plan.time_buckets)
    for join in plan.joins:
        references.extend((join.left, join.right))
    return references


def _resolve_column(
    reference: CatalogRef,
    tables: dict[str, Any],
) -> SchemaColumn | None:
    table = tables.get(reference.table.casefold())
    if table is None:
        return None
    return next(
        (
            column
            for column in table.columns
            if column.name.casefold() == reference.column.casefold()
        ),
        None,
    )


# The planner is told to copy the user's request verbatim into objective, but
# models consistently treat "Write SQL to …" / "Generate SQL for …" as an
# imperative prefix and omit it from the analytical goal they write. Stripping
# these prefixes from both sides before comparing preserves drift detection
# (a changed table name or metric still fails) while eliminating the false
# positive that would otherwise block every sql-generation request phrased this
# way, exhaust the one repair attempt on a non-issue, and surface the opaque
# "I couldn't approve the proposed query plan" message.
_WRITE_SQL_PREFIX = re.compile(
    r"^(?:write(?:\s+me)?(?:\s+some)?\s+sql\s+(?:to|for|that)\s+"
    r"|generate\s+(?:a\s+)?sql\s+(?:query\s+)?(?:to|for|that)\s+"
    r"|draft\s+sql\s+(?:to|for|that)\s+"
    r"|create\s+(?:a\s+)?(?:sql\s+)?query\s+(?:to|for|that)\s+)",
    re.IGNORECASE,
)


def _normalized(value: str) -> str:
    # Punctuation-insensitive: the check exists to stop objective *drift*
    # (a planner substituting its own goal), not to fail a plan because the
    # model dropped a question mark, a comma, or the leading sql-request
    # imperative that precedes the actual analytical goal.
    stripped = _WRITE_SQL_PREFIX.sub("", value.strip())
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in stripped)
    return " ".join(cleaned.split()).casefold()


def _parameter_type_for_column(data_type: str) -> str | None:
    normalized = data_type.upper().split("(", 1)[0].strip()
    return {
        "VARCHAR": "STRING",
        "STRING": "STRING",
        "INTEGER": "INT64",
        "INT64": "INT64",
        "FLOAT": "FLOAT64",
        "FLOAT64": "FLOAT64",
        "BOOLEAN": "BOOL",
        "BOOL": "BOOL",
        "DATE": "DATE",
        "DATETIME": "DATETIME",
        "TIMESTAMP": "TIMESTAMP",
    }.get(normalized)


def _hash_model(value: Any) -> str:
    payload = json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _violation(code: str, message: str, value: Any | None = None) -> PlanViolation:
    evidence = (
        {} if value is None else {"value": sorted(value) if isinstance(value, set) else value}
    )
    return PlanViolation(code=code, message=message, evidence=evidence)
