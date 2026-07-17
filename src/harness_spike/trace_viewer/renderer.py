from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from .models import TraceRun

TEMPLATE_PATH = Path(__file__).parent / "static" / "template.html"


def _serialize_traces(traces: list[TraceRun]) -> str:
    data = []
    for t in traces:
        data.append({
            "run_id": t.run_id,
            "question": t.question,
            "status": t.status,
            "total_duration": t.total_duration,
            "warnings": t.warnings,
            "events": [dataclasses.asdict(e) for e in t.events],
            "nodes": [dataclasses.asdict(n) for n in t.nodes],
            "edges": [dataclasses.asdict(e) for e in t.edges],
        })
    return json.dumps(data, ensure_ascii=True, indent=None)


def render_html(traces: list[TraceRun], output_path: Path) -> Path:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    trace_json = _serialize_traces(traces)
    html = template.replace("/*__TRACE_DATA__*/", f"const TRACE_DATA = {trace_json};")
    output_path.write_text(html, encoding="utf-8")
    return output_path
