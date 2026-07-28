"""Budget enforcement tests (no model calls required)."""

from __future__ import annotations

import pytest

from agent_host.budget import BudgetExceeded, ExecutionBudget


def test_budget_caps_total_and_per_tool_calls() -> None:
    budget = ExecutionBudget(max_tool_calls=2, max_calls_per_tool=1)
    budget.reserve_tool_call("search_tables", {"question": "appointments"})
    budget.reserve_tool_call("get_table_schema", {"table_name": "appointments"})

    with pytest.raises(BudgetExceeded, match="max_tool_calls"):
        budget.reserve_tool_call("search_tables", {"question": "again"})


def test_budget_exclusively_owns_payload_size_limits() -> None:
    budget = ExecutionBudget(
        max_input_bytes=32,
        max_tool_result_bytes=32,
        max_context_bytes=32,
    )
    oversized = {"value": "x" * 100}

    with pytest.raises(BudgetExceeded, match="max_input_bytes"):
        budget.reserve_tool_call("search_tables", oversized)

    with pytest.raises(BudgetExceeded, match="max_tool_result_bytes"):
        budget.accept_tool_result(oversized)

    with pytest.raises(BudgetExceeded, match="max_context_bytes"):
        budget.check_context(oversized)


def test_budget_owns_retrieval_breadth_limit() -> None:
    budget = ExecutionBudget(max_retrieved_chunks=3)

    assert budget.bound_retrieval_count(10) == 3

    with pytest.raises(BudgetExceeded, match="max_retrieved_chunks"):
        ExecutionBudget(max_retrieved_chunks=0).bound_retrieval_count(1)


def test_budget_owns_sql_repair_limit() -> None:
    budget = ExecutionBudget(max_sql_repairs=3)
    assert budget.max_sql_repairs == 3


def test_budget_caps_model_calls() -> None:
    budget = ExecutionBudget(max_model_calls=2)
    budget.reserve_model_call({"content": "hello"})
    budget.reserve_model_call({"content": "world"})

    with pytest.raises(BudgetExceeded, match="max_model_calls"):
        budget.reserve_model_call({"content": "too many"})


def test_budget_caps_per_tool_calls() -> None:
    budget = ExecutionBudget(max_calls_per_tool=1)
    budget.reserve_tool_call("search_tables", {"q": "x"})

    with pytest.raises(BudgetExceeded, match="max_calls_per_tool:search_tables"):
        budget.reserve_tool_call("search_tables", {"q": "y"})


def test_budget_bound_retrieval_is_idempotent_when_equal() -> None:
    budget = ExecutionBudget(max_retrieved_chunks=5)
    assert budget.bound_retrieval_count(5) == 5
    assert budget.bound_retrieval_count(3) == 3


def test_budget_from_env_uses_defaults_when_no_env_vars(monkeypatch) -> None:
    from agent_host.budget import budget_from_env

    # Ensure env vars are not set
    for var in ["MAX_SQL_REPAIRS", "MAX_RETRIEVED_CHUNKS", "MAX_WALL_SECONDS"]:
        monkeypatch.delenv(var, raising=False)

    budget = budget_from_env()
    assert budget.max_sql_repairs == 3
    assert budget.max_retrieved_chunks == 5
    assert budget.max_wall_seconds == 60.0
