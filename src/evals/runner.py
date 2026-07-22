from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent_host.agent import ask
from agent_host.config import get_settings
from agent_host.mcp_bridge import MCPToolBridge
from evals.assertions import EvaluationCase, evaluate_case
from evals.intent_assertions import (
    IntentEvaluationCase,
    evaluate_intent_case,
    summarize_intent_results,
)
from evals.retrieval_evaluator import run_retrieval_evaluation
from mcp_servers.intent import INTENT_PROMPT_VERSION, IntentResult

DEFAULT_CASE_DIR = Path("evals")
DEFAULT_RESULTS_DIR = DEFAULT_CASE_DIR / "results"


def load_cases(path: Path) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw_case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
        cases.append(EvaluationCase.from_dict(raw_case))
    if not cases:
        raise ValueError(f"{path}: no evaluation cases found")
    return cases


def load_intent_cases(path: Path) -> list[IntentEvaluationCase]:
    cases: list[IntentEvaluationCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw_case = json.loads(line)
            cases.append(IntentEvaluationCase.from_dict(raw_case))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{path}:{line_number}: invalid intent evaluation case") from exc
    if not cases:
        raise ValueError(f"{path}: no intent evaluation cases found")
    return cases


def load_trace(path: str | Path) -> list[dict[str, Any]]:
    trace_path = Path(path)
    return [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def run_cases(cases: list[EvaluationCase]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            response = await ask(case.prompt)
            events = load_trace(response["trace_file"])
            failures = evaluate_case(case, response, events)
            results.append(
                {
                    "id": case.id,
                    "category": case.category,
                    "passed": not failures,
                    "run_id": response["run_id"],
                    "failures": [asdict(failure) for failure in failures],
                }
            )
        except Exception as exc:
            results.append(
                {
                    "id": case.id,
                    "category": case.category,
                    "passed": False,
                    "run_id": None,
                    "failures": [{"check": "runner", "message": f"{type(exc).__name__}: {exc}"}],
                }
            )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "total": len(results),
        "passed": sum(1 for result in results if result["passed"]),
        "failed": sum(1 for result in results if not result["passed"]),
        "results": results,
    }


async def run_intent_cases(cases: list[IntentEvaluationCase], repetitions: int) -> dict[str, Any]:
    """Run synthetic labels against the intent MCP without invoking the host."""
    settings = get_settings()
    results: list[dict[str, Any]] = []
    for repetition in range(1, repetitions + 1):
        try:
            async with MCPToolBridge(
                settings.intent_mcp_url,
                auth_token=getattr(settings, "mcp_auth_token", None),
            ) as intent_mcp:
                for case in cases:
                    expected = {
                        "intent": case.expected_intent,
                        "recommended_action": case.expected_recommended_action,
                        "needs_clarification": case.expected_needs_clarification,
                        "safety_class": case.safety_class,
                    }
                    try:
                        raw_result = await intent_mcp.call_tool(
                            "classify_intent", {"question": case.prompt}
                        )
                        result = IntentResult.model_validate(raw_result)
                        failures = evaluate_intent_case(case, result)
                        results.append(
                            {
                                "id": case.id,
                                "round": case.round,
                                "repetition": repetition,
                                "category": case.category,
                                "expected": expected,
                                "observed": result.model_dump(),
                                "passed": not failures,
                                "operational_failure": False,
                                "failures": [asdict(failure) for failure in failures],
                            }
                        )
                    except (Exception, ValidationError) as exc:
                        results.append(
                            {
                                "id": case.id,
                                "round": case.round,
                                "repetition": repetition,
                                "category": case.category,
                                "expected": expected,
                                "observed": None,
                                "passed": False,
                                "operational_failure": True,
                                "failures": [
                                    {
                                        "check": "runner",
                                        "message": f"{type(exc).__name__}: {exc}",
                                    }
                                ],
                            }
                        )
        except Exception as exc:
            for case in cases:
                results.append(
                    {
                        "id": case.id,
                        "round": case.round,
                        "repetition": repetition,
                        "category": case.category,
                        "expected": {
                            "intent": case.expected_intent,
                            "recommended_action": case.expected_recommended_action,
                            "needs_clarification": case.expected_needs_clarification,
                            "safety_class": case.safety_class,
                        },
                        "observed": None,
                        "passed": False,
                        "operational_failure": True,
                        "failures": [
                            {"check": "runner", "message": f"{type(exc).__name__}: {exc}"}
                        ],
                    }
                )

    return {
        "suite": "intent",
        "generated_at": datetime.now(UTC).isoformat(),
        "model": settings.require_claude_model(),
        "intent_prompt_version": INTENT_PROMPT_VERSION,
        "repetitions": repetitions,
        "metrics": summarize_intent_results(results),
        "results": results,
    }


def write_report(report: dict[str, Any], results_dir: Path, prefix: str = "evaluation") -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = results_dir / f"{prefix}-{timestamp}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run synthetic trace-based or direct intent-MCP evaluation cases."
    )
    parser.add_argument(
        "--suite",
        choices=("smoke", "red_team", "intent", "retrieval"),
        default="smoke",
        help="Case suite to run.",
    )
    parser.add_argument(
        "--case-dir",
        type=Path,
        default=DEFAULT_CASE_DIR,
        help="Directory containing suite JSONL files.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help="Directory for generated JSON reports.",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        help="Repeat the intent suite to measure model variability.",
    )
    parser.add_argument(
        "--retriever",
        choices=("fts",),
        default="fts",
        help="Retriever implementation for the retrieval suite.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("var/rag/index.sqlite"),
        help="SQLite RAG index for the retrieval suite.",
    )
    parser.add_argument(
        "--k",
        type=int,
        nargs="+",
        default=(5, 10),
        help="Ranking cutoffs for the retrieval suite.",
    )
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")
    if any(k < 1 for k in args.k):
        parser.error("every --k value must be at least 1")

    if args.suite == "retrieval":
        report = run_retrieval_evaluation(
            db_path=args.db,
            queries_path=args.case_dir / "retrieval_queries.jsonl",
            qrels_path=args.case_dir / "retrieval_qrels.jsonl",
            retriever=args.retriever,
            k_values=args.k,
        )
        report_path = write_report(report, args.results_dir, prefix="retrieval-evaluation")
        chunk_metrics = report["metrics"]["chunk"]
        document_metrics = report["metrics"]["document"]
        largest_k = max(args.k)
        print(f"Evaluated {report['query_count']} retrieval queries with {args.retriever}")
        print(f"Document Recall@{largest_k}: {document_metrics[f'recall@{largest_k}']}")
        print(f"Document nDCG@{largest_k}: {document_metrics[f'ndcg@{largest_k}']}")
        print(f"Chunk Recall@{largest_k}: {chunk_metrics[f'recall@{largest_k}']}")
        print(f"Chunk nDCG@{largest_k}: {chunk_metrics[f'ndcg@{largest_k}']}")
        print(f"Unjudged results: {report['unjudged_total']}")
        print(f"Report: {report_path}")
        if not report["judgment_complete"]:
            raise SystemExit(1)
        return

    if args.suite == "intent":
        cases = load_intent_cases(args.case_dir / "intent.jsonl")
        report = asyncio.run(run_intent_cases(cases, args.repetitions))
        report_path = write_report(report, args.results_dir, prefix="intent-evaluation")
        metrics = report["metrics"]
        print(
            f"{metrics['observed_total']} / {metrics['total']} observations completed; "
            f"{metrics['reported_differences']} labelled differences"
        )
        print(f"Intent accuracy: {metrics['intent_accuracy']}")
        print(f"Unsafe-to-safe routes: {metrics['unsafe_to_safe_routes']}")
        print(f"Report: {report_path}")
        if metrics["operational_failures"]:
            raise SystemExit(1)
        return

    cases = load_cases(args.case_dir / f"{args.suite}.jsonl")
    report = asyncio.run(run_cases(cases))
    report_path = write_report(report, args.results_dir)
    print(f"{report['passed']} / {report['total']} passed")
    for result in report["results"]:
        if not result["passed"]:
            details = "; ".join(failure["message"] for failure in result["failures"])
            print(f"FAIL {result['id']}: {details}")
    print(f"Report: {report_path}")
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
