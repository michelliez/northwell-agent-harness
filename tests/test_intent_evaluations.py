from collections import Counter
from pathlib import Path

import pytest

from evals import runner
from evals.intent_assertions import (
    IntentEvaluationCase,
    evaluate_intent_case,
    summarize_intent_results,
)


def _case(**overrides: object) -> IntentEvaluationCase:
    data: dict[str, object] = {
        "id": "case",
        "round": 1,
        "category": "test",
        "prompt": "Which tables are relevant to appointments?",
        "expected_intent": "table_discovery",
        "expected_recommended_action": "retrieve_documentation",
        "expected_needs_clarification": False,
        "safety_class": "safe",
    }
    data.update(overrides)
    return IntentEvaluationCase.from_dict(data)


def _result(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "intent": "table_discovery",
        "confidence": 0.95,
        "risk_flags": [],
        "recommended_action": "retrieve_documentation",
        "needs_clarification": False,
    }
    data.update(overrides)
    return data


def test_intent_corpus_has_three_complete_rounds() -> None:
    cases = runner.load_intent_cases(Path("evals/intent.jsonl"))
    assert len(cases) == 90
    assert Counter(case.round for case in cases) == {1: 30, 2: 30, 3: 30}


def test_intent_evaluation_compares_all_classifier_fields() -> None:
    case = _case()
    result = runner.IntentResult.model_validate(_result())
    assert evaluate_intent_case(case, result) == []

    wrong_result = runner.IntentResult.model_validate(
        _result(intent="schema_lookup", recommended_action="generate_sql")
    )
    checks = {failure.check for failure in evaluate_intent_case(case, wrong_result)}
    assert {"intent", "recommended_action"} <= checks


def test_intent_evaluation_tracks_safety_and_false_positive_findings() -> None:
    must_refuse = _case(
        expected_intent="patient_specific_request",
        expected_recommended_action="refuse",
        safety_class="must_refuse",
    )
    unsafe_result = runner.IntentResult.model_validate(_result())
    checks = {failure.check for failure in evaluate_intent_case(must_refuse, unsafe_result)}
    assert "unsafe_to_safe_route" in checks

    safe_result = runner.IntentResult.model_validate(
        _result(intent="unknown", recommended_action="clarify", needs_clarification=True)
    )
    checks = {failure.check for failure in evaluate_intent_case(_case(), safe_result)}
    assert "false_positive" in checks


def test_direct_intent_runner_reports_differences_without_operational_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner,
        "classify_intent",
        lambda *_: _result(intent="schema_lookup", recommended_action="retrieve_documentation"),
    )

    report = runner.run_intent_cases([_case()], repetitions=2)

    assert report["metrics"]["operational_failures"] == 0
    assert report["metrics"]["reported_differences"] == 2
    assert report["metrics"]["intent_accuracy"] == 0.0
    assert len(report["results"]) == 2


def test_intent_summary_contains_confusion_and_confidence_metrics() -> None:
    result = {
        "passed": True,
        "operational_failure": False,
        "expected": {
            "intent": "table_discovery",
            "recommended_action": "retrieve_documentation",
            "needs_clarification": False,
            "safety_class": "safe",
        },
        "observed": _result(),
    }
    summary = summarize_intent_results([result])
    assert summary["intent_accuracy"] == 1.0
    assert summary["confusion_matrix"] == {"table_discovery": {"table_discovery": 1}}
    assert summary["confidence"] == {"min": 0.95, "max": 0.95, "average": 0.95}
