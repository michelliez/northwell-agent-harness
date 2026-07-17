from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from .parser import load_traces
from .renderer import render_html


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an interactive trace diagram from JSONL trace files.")
    parser.add_argument("input", nargs="+", help="JSONL file(s) or directory of JSONL files")
    parser.add_argument("-o", "--output", default="trace_view.html", help="Output HTML path (default: trace_view.html)")
    parser.add_argument("--no-open", action="store_true", help="Don't open the browser automatically")
    args = parser.parse_args()

    paths = [Path(p) for p in args.input]
    traces, warnings = load_traces(paths)

    if warnings:
        for w in warnings:
            print(f"  warning: {w}", file=sys.stderr)

    if not traces:
        print("No traces found.", file=sys.stderr)
        sys.exit(1)

    output = Path(args.output)
    render_html(traces, output)
    print(f"Wrote {output} ({len(traces)} trace(s))")

    if not args.no_open:
        webbrowser.open(output.resolve().as_uri())


if __name__ == "__main__":
    main()
