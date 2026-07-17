from __future__ import annotations

import argparse
import asyncio
import json
import platform
import subprocess
import webbrowser
from pathlib import Path
from typing import Any

from harness_spike.agent_host.agent import ask
from harness_spike.trace_viewer.parser import load_traces
from harness_spike.trace_viewer.renderer import render_html


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MCP data-catalog prototype.")
    parser.add_argument("prompt", nargs="+", help="User prompt to send to Claude.")
    parser.add_argument("--json", action="store_true", help="Print raw JSON result.")
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Print the JSONL trace after the answer.",
    )
    parser.add_argument(
        "--viewer",
        action="store_true",
        help="Render and open an HTML trace viewer for this run.",
    )
    parser.add_argument(
        "--no-open-viewer",
        action="store_true",
        help="Render the trace viewer without opening it in a browser.",
    )
    args = parser.parse_args()

    prompt = " ".join(args.prompt)
    result = asyncio.run(ask(prompt))
    viewer_path = render_trace_viewer(result) if args.viewer else None
    viewer_url = viewer_path.resolve().as_uri() if viewer_path is not None else None
    if viewer_url is not None and not args.no_open_viewer:
        open_viewer(viewer_url)

    if args.json:
        if viewer_path is not None:
            result["trace_viewer"] = str(viewer_path)
            result["trace_viewer_url"] = viewer_url
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return

    print("\nAnswer:")
    print(result["answer"])
    print(f"\nUsed tools: {format_tools(result)}")
    print(f"Run ID:     {result['run_id']}")
    print(f"Trace:      {result['trace_file']}")
    if viewer_path is not None and viewer_url is not None:
        print(f"Viewer:     {viewer_url}")
        print(f"Viewer file:{viewer_path.resolve()}")

    if args.trace:
        print("\nTrace:")
        print(Path(str(result["trace_file"])).read_text(encoding="utf-8"))


def format_tools(result: dict[str, Any]) -> str:
    used_tools = result.get("used_tools", [])
    if not used_tools:
        return "none"
    return ", ".join(str(tool) for tool in used_tools)


def render_trace_viewer(result: dict[str, Any]) -> Path:
    trace_file = Path(str(result["trace_file"]))
    traces, warnings = load_traces([trace_file])
    if not traces:
        raise RuntimeError(f"No trace data found in {trace_file}")

    output = Path("logs") / "trace_views" / f"trace_view_{result['run_id']}.html"
    render_html(traces, output)

    for warning in warnings:
        print(f"warning: {warning}")
    return output


def open_viewer(viewer_url: str) -> None:
    opened = False
    if platform.system() == "Darwin":
        completed = subprocess.run(["open", viewer_url], check=False)
        opened = completed.returncode == 0

    if not opened:
        opened = webbrowser.open(viewer_url, new=2)

    if not opened:
        print(f"warning: could not auto-open viewer; open this URL manually: {viewer_url}")


if __name__ == "__main__":
    main()
