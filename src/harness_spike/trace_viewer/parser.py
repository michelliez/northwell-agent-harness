from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .classifier import classify_event
from .models import GraphEdge, GraphNode, TraceEvent, TraceRun
from .redaction import redact


def _extract_label(event: str, raw: dict[str, Any]) -> str:
    if event == "request.received":
        return "Request Received"
    if event == "policy_gate.checked":
        result = raw.get("result", {})
        if result.get("allowed"):
            return "Policy Gate: Allowed"
        return "Policy Gate: Blocked"
    if event == "request.blocked":
        return f"Blocked: {raw.get('reason', 'unknown')}"
    if event == "intent.classification.request":
        return "Intent Classification"
    if event == "intent.classification.result":
        result = raw.get("result", {})
        intent = result.get("intent", "?")
        conf = result.get("confidence", 0)
        return f"Intent: {intent} ({conf:.0%})"
    if event == "intent.classification.failed":
        return f"Intent Failed: {raw.get('error', 'unknown')}"
    if event == "general_question.started":
        return "General Question"
    if event.startswith("sql.workflow"):
        return event.replace("sql.", "SQL ").replace(".", " ").title()
    if event == "sql.generation.refused":
        return "SQL Generation Refused"
    if event == "mcp.tools.listed":
        tools = raw.get("tools", [])
        return f"Tools Listed ({len(tools)})"
    if event in ("model.request", "model.request.first", "model.request.final"):
        model = raw.get("model", "")
        rd = raw.get("round", "")
        return f"Model Request R{rd}" if rd else f"Model Request ({model})"
    if event in ("model.response", "model.response.first", "model.response.final"):
        rd = raw.get("round", "")
        return f"Model Response R{rd}" if rd else "Model Response"
    if event == "tool.selected":
        name = raw.get("name", "?")
        rd = raw.get("round", "")
        return f"Tool: {name}" + (f" R{rd}" if rd else "")
    if event == "tool.result":
        name = raw.get("name", "?")
        return f"Result: {name}"
    if event == "answer.ready":
        return "Answer Ready"
    return event


def _extract_summary(event: str, raw: dict[str, Any]) -> str:
    if event == "request.received":
        q = raw.get("question", "")
        if q == "[hidden]":
            return "(question hidden)"
        return q[:120] + ("..." if len(q) > 120 else "")
    if event == "policy_gate.checked":
        result = raw.get("result", {})
        if not result.get("allowed"):
            return f"Blocked: {result.get('matched_term', '')}"
        return result.get("reason") or "Allowed"
    if event == "request.blocked":
        return raw.get("reason", "")
    if event == "intent.classification.result":
        result = raw.get("result", {})
        action = result.get("recommended_action", "")
        return f"Action: {action}"
    if event == "tool.selected":
        inp = raw.get("input", {})
        return json.dumps(inp, ensure_ascii=False)[:100]
    if event == "tool.result":
        result = raw.get("result", {})
        if isinstance(result, dict):
            candidates = result.get("candidates", [])
            if candidates:
                return f"{len(candidates)} candidates"
        return str(result)[:100]
    if event == "answer.ready":
        answer = raw.get("answer", "")
        return answer[:120] + ("..." if len(answer) > 120 else "")
    return ""


def _extract_status(event: str, raw: dict[str, Any]) -> str:
    if event == "request.blocked":
        return "blocked"
    if event in ("intent.classification.failed", "agent.max_rounds_reached"):
        return "error"
    if event == "sql.generation.refused":
        return "refused"
    if event in ("sql.validation.blocked", "sql.validation.failed"):
        return "failed"
    if event == "policy_gate.checked":
        result = raw.get("result", {})
        return "success" if result.get("allowed") else "blocked"
    return "success"


def _extract_input(event: str, raw: dict[str, Any]) -> dict[str, Any]:
    if event == "request.received":
        return {"question": raw.get("question")}
    if event == "intent.classification.request":
        return {"mcp_url": raw.get("mcp_url")}
    if event == "tool.selected":
        return {"tool": raw.get("name"), "input": raw.get("input")}
    if event in ("model.request", "model.request.first", "model.request.final"):
        return {
            "model": raw.get("model"),
            "round": raw.get("round"),
            "tools": raw.get("tools"),
        }
    return {}


def _extract_output(event: str, raw: dict[str, Any]) -> dict[str, Any]:
    if event == "policy_gate.checked":
        return raw.get("result", {})
    if event == "request.blocked":
        return {"reason": raw.get("reason"), "matched_term": raw.get("matched_term")}
    if event == "intent.classification.result":
        return raw.get("result", {})
    if event == "intent.classification.failed":
        return {"error": raw.get("error")}
    if event == "tool.result":
        return {"tool": raw.get("name"), "result": raw.get("result")}
    if event in ("model.response", "model.response.first", "model.response.final"):
        return {"round": raw.get("round"), "response": raw.get("response")}
    if event == "answer.ready":
        return {
            "answer": raw.get("answer"),
            "used_tools": raw.get("used_tools"),
        }
    if event == "sql.workflow.completed":
        return {"sql": raw.get("sql"), "used_tools": raw.get("used_tools")}
    if event == "sql.generation.refused":
        return raw.get("result", {})
    return {}


def _edge_label(prev_event: str, cur_event: str, prev_raw: dict[str, Any]) -> str:
    if prev_event == "policy_gate.checked":
        result = prev_raw.get("result", {})
        return "blocked" if not result.get("allowed") else "allowed"
    if prev_event == "intent.classification.result":
        result = prev_raw.get("result", {})
        action = result.get("recommended_action", "")
        return action if action else "next"
    if prev_event == "model.response" and cur_event == "tool.selected":
        return "tool_call"
    if prev_event == "tool.result" and cur_event == "model.request":
        return "continue"
    if prev_event == "tool.result" and cur_event == "tool.selected":
        return "next_tool"
    if cur_event == "answer.ready":
        return "complete"
    if cur_event == "request.blocked":
        return "blocked"
    return ""


def _edge_type(label: str) -> str:
    if label == "blocked":
        return "blocked"
    if label in ("error", "failed"):
        return "error"
    return "default"


def parse_file(path: Path) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Parse a JSONL file into run_id-grouped raw events + warnings."""
    runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    warnings: list[str] = []

    with path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                warnings.append(f"{path.name}:{line_num}: malformed JSON: {exc}")
                continue
            if not isinstance(record, dict):
                warnings.append(f"{path.name}:{line_num}: expected object, got {type(record).__name__}")
                continue
            run_id = record.get("run_id")
            if not run_id:
                warnings.append(f"{path.name}:{line_num}: missing run_id")
                continue
            runs[run_id].append(record)

    return dict(runs), warnings


def build_trace_run(run_id: str, raw_events: list[dict[str, Any]]) -> TraceRun:
    """Build a normalized TraceRun from raw event dicts."""
    raw_events.sort(key=lambda e: e.get("ts", 0) or 0)
    events: list[TraceEvent] = []
    warnings: list[str] = []

    for idx, raw in enumerate(raw_events):
        event_type = raw.get("event", "unknown")
        ts = raw.get("ts")
        duration = None
        if ts and idx + 1 < len(raw_events):
            next_ts = raw_events[idx + 1].get("ts")
            if next_ts:
                duration = round(next_ts - ts, 4)

        events.append(
            TraceEvent(
                event_id=idx,
                run_id=run_id,
                event_type=event_type,
                node_type=classify_event(event_type),
                ts=ts,
                duration=duration,
                status=_extract_status(event_type, raw),
                label=_extract_label(event_type, raw),
                summary=_extract_summary(event_type, raw),
                input_data=redact(_extract_input(event_type, raw)),
                output_data=redact(_extract_output(event_type, raw)),
                raw=redact(raw),
                round_number=raw.get("round"),
                tool_name=raw.get("name") if "tool" in event_type else None,
                model_name=raw.get("model"),
                error=raw.get("error"),
            )
        )

    nodes = [
        GraphNode(
            id=f"n{e.event_id}",
            label=e.label,
            node_type=e.node_type,
            status=e.status,
            duration=e.duration,
            summary=e.summary,
            event_idx=e.event_id,
            round_number=e.round_number,
        )
        for e in events
    ]

    edges = []
    for i in range(len(events) - 1):
        prev = events[i]
        cur = events[i + 1]
        label = _edge_label(prev.event_type, cur.event_type, raw_events[i])
        edges.append(
            GraphEdge(
                source=f"n{prev.event_id}",
                target=f"n{cur.event_id}",
                label=label,
                edge_type=_edge_type(label),
            )
        )

    question = ""
    if events and events[0].event_type == "request.received":
        question = events[0].summary

    status = "unknown"
    if events:
        last = events[-1]
        if last.event_type == "answer.ready":
            status = "success"
        elif last.event_type == "request.blocked":
            status = "blocked"
        elif last.status == "error":
            status = "error"
        elif last.event_type == "intent.classification.failed":
            status = "error"

    total_duration = None
    if events and events[0].ts and events[-1].ts:
        total_duration = round(events[-1].ts - events[0].ts, 4)

    return TraceRun(
        run_id=run_id,
        events=events,
        nodes=nodes,
        edges=edges,
        question=question,
        status=status,
        total_duration=total_duration,
        warnings=warnings,
    )


def load_traces(paths: list[Path]) -> tuple[list[TraceRun], list[str]]:
    """Load traces from files or directories."""
    all_warnings: list[str] = []
    all_raw: dict[str, list[dict[str, Any]]] = {}

    file_list: list[Path] = []
    for p in paths:
        if p.is_dir():
            file_list.extend(sorted(p.glob("*.jsonl")))
        elif p.is_file():
            file_list.append(p)
        else:
            all_warnings.append(f"Path not found: {p}")

    for fp in file_list:
        runs, warnings = parse_file(fp)
        all_warnings.extend(warnings)
        for run_id, events in runs.items():
            all_raw.setdefault(run_id, []).extend(events)

    traces = []
    for run_id, raw_events in all_raw.items():
        trace = build_trace_run(run_id, raw_events)
        trace.warnings.extend(all_warnings)
        traces.append(trace)

    traces.sort(key=lambda t: t.events[0].ts or 0 if t.events else 0)
    return traces, all_warnings
