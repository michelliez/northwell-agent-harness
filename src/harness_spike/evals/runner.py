from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness_spike.agent_host.agent import ask
from harness_spike.evals.assertions import EvaluationCase, evaluate_case


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
                    "failures": [
                        {"check": "runner", "message": f"{type(exc).__name__}: {exc}"}
                    ],
                }
            )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": sum(1 for result in results if result["passed"]),
        "failed": sum(1 for result in results if not result["passed"]),
        "results": results,
    }


def write_report(report: dict[str, Any], results_dir: Path) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = results_dir / f"evaluation-{timestamp}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run deterministic trace-based evaluation cases."
    )
    parser.add_argument(
        "--suite",
        choices=("smoke", "red_team"),
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
    args = parser.parse_args()

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
