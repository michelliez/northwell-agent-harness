from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from harness_spike.agent_host.agent import ask


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Anthropic + MCP weather spike.")
    parser.add_argument("prompt", nargs="+", help="User prompt to send to Claude.")
    parser.add_argument("--json", action="store_true", help="Print raw JSON result.")
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Print the JSONL trace after the answer.",
    )
    args = parser.parse_args()

    prompt = " ".join(args.prompt)
    result = asyncio.run(ask(prompt))

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return

    print("\nAnswer:")
    print(result["answer"])
    print(f"\nUsed tools: {format_tools(result)}")
    print(f"Run ID:     {result['run_id']}")
    print(f"Trace:      {result['trace_file']}")

    if args.trace:
        print("\nTrace:")
        print(Path(str(result["trace_file"])).read_text(encoding="utf-8"))


def format_tools(result: dict[str, Any]) -> str:
    used_tools = result.get("used_tools", [])
    if not used_tools:
        return "none"
    return ", ".join(str(tool) for tool in used_tools)


if __name__ == "__main__":
    main()
