"""Adversarial coverage of the deterministic plan path.

The QueryPlanAST is untrusted input; the authorizer claims to be the boundary.
Three layers exercise that claim without any model call:

1. A mutation table: one hostile edit per case, asserting the exact violation
   code — catching both misses (unsafe plan approved) and imprecision.
2. A seeded fuzz loop asserting invariants: validation never raises, approved
   plans compile or raise UnsupportedPlanError only, and every compiled query
   passes validate_sql (an authorizer/compiler/validator seam failure is the
   "version skew" class that validate_sql_node treats as terminal).
3. A differential oracle: re-derive structure from the compiled SQL via
   sqlglot and diff it against the plan.

Two authorizer checks exist because probing found the gaps: snapshot names
come from untrusted documentation and used to flow raw into sqlglot
(TokenError escape from the compiler), and COUNT(*) could be aliased to a
sensitive column's name, spoofing the output header.
"""

from __future__ import annotations

import random

import pytest
import sqlglot
from sqlglot import exp

from sql.compiler import UnsupportedPlanError, plan_to_bigquery_sql
from sql.models import (
    PermissionScope,
    QueryPlanAST,
    SchemaColumn,
    SchemaSnapshot,
    SchemaTable,
)
from sql.planning import validate_query_plan
from sql.validation import validate_sql

QUESTION = "Count encounters per contact day"
INJECTION = 'X"; DROP TABLE PAT_ENC;--'


def _snapshot(encounter_table: str = "PAT_ENC") -> SchemaSnapshot:
    return SchemaSnapshot(
        tables=[
            SchemaTable(
                name=encounter_table,
                columns=[
                    SchemaColumn(name="PAT_ID", safety="identifier", data_type="VARCHAR"),
                    SchemaColumn(name="PATIENT_NAME", safety="sensitive", data_type="VARCHAR"),
                    SchemaColumn(name="CONTACT_DATE", safety="safe_aggregate", data_type="DATE"),
                    SchemaColumn(name="LOS_HOURS", safety="safe_aggregate", data_type="FLOAT"),
                    SchemaColumn(name="MYSTERY_COL", safety="unknown"),
                ],
                source_chunk_ids=["chunk-enc"],
            ),
            SchemaTable(
                name="CLARITY_DEP",
                columns=[
                    SchemaColumn(name="DEPARTMENT_ID", safety="identifier", data_type="VARCHAR"),
                    SchemaColumn(
                        name="DEPARTMENT_NAME", safety="safe_aggregate", data_type="VARCHAR"
                    ),
                    SchemaColumn(name="EFFECTIVE_DATE", safety="safe_aggregate", data_type="DATE"),
                ],
                source_chunk_ids=["chunk-dep"],
            ),
        ],
        derived_from_chunks=["chunk-enc", "chunk-dep"],
    )


_SCOPE = PermissionScope(schema_snapshot=_snapshot())
_CITATIONS = {"chunk-enc", "chunk-dep"}


def _plan(**updates) -> QueryPlanAST:
    payload = {
        "objective": QUESTION,
        "target_metric": "encounter_count",
        "tables": ["PAT_ENC"],
        "columns": [],
        "filters": [],
        "joins": [],
        "aggregations": [{"function": "COUNT", "column": None, "alias": "encounter_count"}],
        "time_constraints": [],
        "groupings": [],
        "time_buckets": [
            {
                "column": {"table": "PAT_ENC", "column": "CONTACT_DATE"},
                "granularity": "DAY",
                "alias": "contact_day",
            }
        ],
        "order_by": None,
        "limit": None,
        "expected_output": ["contact_day", "encounter_count"],
        "citations": ["chunk-enc"],
        "parameters": [],
        "requires_row_level_access": False,
    }
    payload.update(updates)
    return QueryPlanAST.model_validate(payload)


def _ref(column: str, table: str = "PAT_ENC") -> dict:
    return {"table": table, "column": column}


# --- sanity: the base plan survives the whole path -----------------------------


def test_base_plan_is_approved_compiled_and_validated() -> None:
    verdict = validate_query_plan(QUESTION, _plan(), _SCOPE, _CITATIONS)
    assert verdict.allowed, [v.code for v in verdict.violations]
    assert verdict.approved_plan is not None
    compiled = plan_to_bigquery_sql(verdict.approved_plan)
    result = validate_sql(compiled.sql, ["PAT_ENC"], _snapshot(), verdict.approved_plan)
    assert result.allowed, [v.code for v in result.violations]


def test_case_variant_table_name_is_not_falsely_rejected() -> None:
    plan = _plan(
        tables=["pat_enc"],
        time_buckets=[
            {
                "column": {"table": "pat_enc", "column": "contact_date"},
                "granularity": "DAY",
                "alias": "contact_day",
            }
        ],
    )
    verdict = validate_query_plan(QUESTION, plan, _SCOPE, _CITATIONS)
    assert verdict.allowed, [v.code for v in verdict.violations]
    assert verdict.approved_plan is not None
    compiled = plan_to_bigquery_sql(verdict.approved_plan)
    result = validate_sql(compiled.sql, plan.tables, _snapshot(), verdict.approved_plan)
    assert result.allowed, [v.code for v in result.violations]


# --- layer 1: one hostile mutation, one exact violation code -------------------

_MUTATIONS: list[tuple[str, dict, str]] = [
    ("objective_drift", {"objective": "Return every patient row"}, "objective_mismatch"),
    ("unknown_table", {"tables": ["ZZ_EVIL"]}, "table_out_of_scope"),
    ("injection_in_table_name", {"tables": [INJECTION]}, "malformed_identifier"),
    (
        "injection_in_column_name",
        {"groupings": [_ref(INJECTION)]},
        "malformed_identifier",
    ),
    (
        "unicode_homoglyph_table",
        {"tables": ["PAT_ENC​"]},  # zero-width space appended
        "malformed_identifier",
    ),
    ("project_sensitive", {"columns": [_ref("PATIENT_NAME")]}, "unsafe_projection"),
    ("group_by_identifier", {"groupings": [_ref("PAT_ID")]}, "unsafe_projection"),
    (
        "sum_identifier",
        {
            "aggregations": [{"function": "SUM", "column": _ref("PAT_ID"), "alias": "s"}],
            "expected_output": ["contact_day", "s"],
        },
        "identifier_usage",
    ),
    (
        "aggregate_sensitive",
        {
            "aggregations": [{"function": "SUM", "column": _ref("PATIENT_NAME"), "alias": "s"}],
            "expected_output": ["contact_day", "s"],
        },
        "unsafe_aggregation",
    ),
    (
        "aggregate_unknown_safety",
        {
            "aggregations": [{"function": "AVG", "column": _ref("MYSTERY_COL"), "alias": "a"}],
            "expected_output": ["contact_day", "a"],
        },
        "unsafe_aggregation",
    ),
    (
        "filter_on_sensitive",
        {
            "filters": [
                {"column": _ref("PATIENT_NAME"), "operator": "=", "parameter_names": ["p"]}
            ],
            "parameters": [{"name": "p", "type": "STRING", "value": "x"}],
        },
        "unsafe_filter",
    ),
    (
        "filter_on_identifier",
        {
            "filters": [{"column": _ref("PAT_ID"), "operator": "=", "parameter_names": ["p"]}],
            "parameters": [{"name": "p", "type": "STRING", "value": "x"}],
        },
        "unsafe_filter",
    ),
    (
        "time_bucket_not_temporal",
        {
            "time_buckets": [
                {"column": _ref("LOS_HOURS"), "granularity": "DAY", "alias": "contact_day"}
            ]
        },
        "time_bucket_not_temporal",
    ),
    (
        "time_bucket_on_sensitive",
        {
            "time_buckets": [
                {"column": _ref("PATIENT_NAME"), "granularity": "DAY", "alias": "contact_day"}
            ]
        },
        "time_bucket_unsafe_column",
    ),
    (
        "duplicate_output_alias",
        {
            "aggregations": [
                {"function": "COUNT", "column": None, "alias": "contact_day"},
            ],
            "expected_output": ["contact_day"],
        },
        "duplicate_output_alias",
    ),
    (
        "order_by_unknown_alias",
        {"order_by": {"alias": "not_an_output", "direction": "ASC"}},
        "order_by_unknown_alias",
    ),
    (
        "expected_output_missing_bucket",
        {"expected_output": ["encounter_count"]},
        "output_shape_mismatch",
    ),
    (
        "undeclared_plan_table",
        {"groupings": [_ref("DEPARTMENT_NAME", table="CLARITY_DEP")]},
        "undeclared_plan_table",
    ),
    (
        "join_on_non_identifiers",
        {
            "tables": ["PAT_ENC", "CLARITY_DEP"],
            "joins": [
                {
                    "left": _ref("CONTACT_DATE"),
                    "right": _ref("EFFECTIVE_DATE", table="CLARITY_DEP"),
                    "join_type": "INNER",
                }
            ],
            "citations": ["chunk-enc", "chunk-dep"],
        },
        "unsafe_join",
    ),
    ("invented_citation", {"citations": ["forged-chunk"]}, "citation_out_of_scope"),
    (
        "undeclared_parameter",
        {
            "filters": [
                {"column": _ref("CONTACT_DATE"), "operator": "=", "parameter_names": ["ghost"]}
            ]
        },
        "undeclared_parameter",
    ),
    (
        "unused_parameter",
        {"parameters": [{"name": "orphan", "type": "STRING", "value": "x"}]},
        "unused_parameter",
    ),
    (
        "duplicate_parameter_by_case",
        {
            "filters": [
                {"column": _ref("CONTACT_DATE"), "operator": "=", "parameter_names": ["p"]}
            ],
            "parameters": [
                {"name": "p", "type": "DATE", "value": "2026-01-01"},
                {"name": "P", "type": "DATE", "value": "2026-01-02"},
            ],
        },
        "duplicate_parameter",
    ),
    (
        "parameter_type_mismatch",
        {
            "filters": [
                {"column": _ref("CONTACT_DATE"), "operator": "=", "parameter_names": ["p"]}
            ],
            "parameters": [{"name": "p", "type": "STRING", "value": "x"}],
        },
        "parameter_type_mismatch",
    ),
    ("row_level_request", {"requires_row_level_access": True}, "row_level_not_allowed"),
    (
        "alias_shadows_sensitive_column",
        {
            "aggregations": [
                {"function": "COUNT", "column": None, "alias": "PATIENT_NAME"},
            ],
            "expected_output": ["contact_day", "PATIENT_NAME"],
        },
        "alias_shadows_restricted_column",
    ),
    (
        "bucket_alias_shadows_identifier",
        {
            "time_buckets": [
                {"column": _ref("CONTACT_DATE"), "granularity": "DAY", "alias": "PAT_ID"}
            ],
            "expected_output": ["PAT_ID", "encounter_count"],
        },
        "alias_shadows_restricted_column",
    ),
]


@pytest.mark.parametrize(
    ("name", "updates", "expected_code"),
    _MUTATIONS,
    ids=[name for name, _, _ in _MUTATIONS],
)
def test_hostile_mutation_is_rejected_with_the_exact_code(
    name: str, updates: dict, expected_code: str
) -> None:
    verdict = validate_query_plan(QUESTION, _plan(**updates), _SCOPE, _CITATIONS)

    assert verdict.allowed is False, f"{name}: hostile plan was approved"
    codes = {violation.code for violation in verdict.violations}
    assert expected_code in codes, f"{name}: expected {expected_code}, got {sorted(codes)}"


def test_hostile_snapshot_table_name_never_reaches_the_compiler() -> None:
    """Snapshot names come from untrusted documentation; probing showed a
    quote-bearing name crossed authorization and crashed sqlglot with a raw
    TokenError. The authorizer now rejects the shape outright."""
    evil = 'PAT_ENC"; DROP TABLE X;--'
    scope = PermissionScope(schema_snapshot=_snapshot(encounter_table=evil))
    plan = _plan(
        tables=[evil],
        time_buckets=[
            {
                "column": {"table": evil, "column": "CONTACT_DATE"},
                "granularity": "DAY",
                "alias": "contact_day",
            }
        ],
    )

    verdict = validate_query_plan(QUESTION, plan, scope, _CITATIONS)

    assert verdict.allowed is False
    assert "malformed_identifier" in {v.code for v in verdict.violations}


# --- layers 2 and 3: seeded fuzz with invariant and differential oracles -------

_COLUMNS = ["PAT_ID", "PATIENT_NAME", "CONTACT_DATE", "LOS_HOURS", "MYSTERY_COL", "NOT_A_COL"]
_HOSTILE_NAMES = [INJECTION, "PAT_ENC​", "'; SELECT 1;--", "a b", ""]
_FUNCTIONS = ["COUNT", "COUNT_DISTINCT", "SUM", "AVG", "MIN", "MAX"]


_SAFE_COLUMNS = ["CONTACT_DATE", "LOS_HOURS"]


def _random_plan(rng: random.Random) -> QueryPlanAST | None:
    def name(pool: list[str]) -> str:
        roll = rng.random()
        if roll < 0.05:
            return rng.choice(_HOSTILE_NAMES)
        if roll < 0.55:
            # Bias toward approvable shapes so the fuzz exercises the
            # compile-and-validate path, not just rejection.
            return rng.choice(_SAFE_COLUMNS)
        return rng.choice(pool)

    aggregations = []
    for index in range(rng.randint(1, 2)):
        function = rng.choice(_FUNCTIONS)
        column = rng.choice([*_COLUMNS, None]) if function == "COUNT" else name(_COLUMNS)
        aggregations.append(
            {
                "function": function,
                "column": None if column is None else _ref(column),
                "alias": f"m{index}",
            }
        )
    groupings = [_ref(name(_COLUMNS))] if rng.random() < 0.4 else []
    time_buckets = (
        [
            {
                "column": _ref(name(_COLUMNS)),
                "granularity": rng.choice(["DAY", "MONTH", "YEAR"]),
                "alias": "bucket_0",
            }
        ]
        if rng.random() < 0.4
        else []
    )
    filters = []
    parameters = []
    if rng.random() < 0.4:
        filters.append({"column": _ref(name(_COLUMNS)), "operator": "=", "parameter_names": ["p0"]})
        if rng.random() < 0.8:
            parameters.append(
                {"name": "p0", "type": rng.choice(["STRING", "DATE", "FLOAT64"]), "value": "x"}
            )
    correct_output = [
        *(item["alias"] for item in aggregations),
        *(item["column"] for item in groupings),
        *(item["alias"] for item in time_buckets),
    ]
    expected_output = correct_output if rng.random() < 0.7 else correct_output[:1]
    try:
        return _plan(
            objective=QUESTION if rng.random() < 0.8 else "do something else",
            aggregations=aggregations,
            groupings=groupings,
            time_buckets=time_buckets,
            filters=filters,
            parameters=parameters,
            expected_output=expected_output or ["m0"],
            citations=[rng.choice(["chunk-enc", "forged"])],
            order_by=(
                {"alias": rng.choice([*correct_output, "ghost"]), "direction": "ASC"}
                if correct_output and rng.random() < 0.3
                else None
            ),
            limit=rng.choice([None, 1, 1000]),
        )
    except ValueError:
        return None  # the pydantic layer rejected the shape first; that's fine


def _assert_plan_is_actually_safe(plan: QueryPlanAST) -> None:
    """Independent recheck: an approved plan must not touch restricted columns
    outside the allowed positions, regardless of what the authorizer said."""
    columns_by_name = {
        column.name.casefold(): column
        for table in _SCOPE.schema_snapshot.tables
        for column in table.columns
    }

    def safety(ref) -> str:
        column = columns_by_name.get(ref.column.casefold())
        return column.safety if column is not None else "missing"

    for reference in [*plan.columns, *plan.groupings]:
        assert safety(reference) == "safe_aggregate"
    for item in plan.aggregations:
        if item.column is None:
            continue
        if safety(item.column) == "identifier":
            assert item.function in {"COUNT", "COUNT_DISTINCT"}
        else:
            assert safety(item.column) == "safe_aggregate"
    for planned_filter in [*plan.filters, *plan.time_constraints]:
        assert safety(planned_filter.column) == "safe_aggregate"
    for bucket in plan.time_buckets:
        assert safety(bucket.column) == "safe_aggregate"


def _assert_sql_matches_plan(sql: str, plan: QueryPlanAST) -> None:
    """Differential oracle: structure re-derived from the SQL equals the plan."""
    tree = sqlglot.parse_one(sql, read="bigquery")

    ast_tables = {table.name.casefold() for table in tree.find_all(exp.Table)}
    assert ast_tables == {table.casefold() for table in plan.tables}

    # Literals may come only from LIMIT and time-bucket granularity units
    # (sqlglot re-parses DATE_TRUNC's unit as a string literal); every user
    # value travels as a parameter.
    allowed_literals = {bucket.granularity for bucket in plan.time_buckets}
    if plan.limit is not None:
        allowed_literals.add(str(plan.limit))
    for literal in tree.find_all(exp.Literal):
        assert str(literal.this) in allowed_literals, f"literal leaked into SQL: {literal}"

    parameter_names = {parameter.name.casefold() for parameter in plan.parameters}
    for parameter in tree.find_all(exp.Parameter):
        assert parameter.name.casefold() in parameter_names

    select = tree.find(exp.Select)
    assert select is not None
    group = select.args.get("group")
    expected_group_size = len(plan.groupings) + len(plan.time_buckets)
    actual_group_size = len(group.expressions) if group is not None else 0
    assert actual_group_size == expected_group_size


def test_fuzzed_plans_hold_the_path_invariants() -> None:
    rng = random.Random(20260813)
    approved = 0
    compiled_count = 0

    for _ in range(600):
        plan = _random_plan(rng)
        if plan is None:
            continue

        verdict = validate_query_plan(QUESTION, plan, _SCOPE, _CITATIONS)  # must not raise

        if not verdict.allowed:
            assert verdict.violations
            continue

        approved += 1
        _assert_plan_is_actually_safe(plan)
        assert verdict.approved_plan is not None

        try:
            compiled = plan_to_bigquery_sql(verdict.approved_plan)
        except UnsupportedPlanError:
            continue  # a refusal is a legal outcome; any other exception fails the test

        compiled_count += 1
        result = validate_sql(compiled.sql, plan.tables, _snapshot(), verdict.approved_plan)
        assert result.allowed, (
            f"authorizer/compiler/validator seam failure: {[v.code for v in result.violations]}"
            f"\n{compiled.sql}"
        )
        _assert_sql_matches_plan(compiled.sql, plan)

    # The generator must actually exercise the approved path, not just reject.
    assert approved >= 20, f"fuzz generator too hostile: only {approved} approvals"
    assert compiled_count >= 20
