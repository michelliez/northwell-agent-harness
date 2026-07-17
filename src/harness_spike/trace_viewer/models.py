from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TraceEvent:
    event_id: int
    run_id: str
    event_type: str
    node_type: str
    ts: float | None
    duration: float | None
    status: str
    label: str
    summary: str
    input_data: dict[str, Any]
    output_data: dict[str, Any]
    raw: dict[str, Any]
    round_number: int | None = None
    tool_name: str | None = None
    model_name: str | None = None
    error: str | None = None


@dataclass
class GraphNode:
    id: str
    label: str
    node_type: str
    status: str
    duration: float | None
    summary: str
    event_idx: int
    round_number: int | None = None


@dataclass
class GraphEdge:
    source: str
    target: str
    label: str
    edge_type: str


@dataclass
class TraceRun:
    run_id: str
    events: list[TraceEvent]
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    question: str = ""
    status: str = "unknown"
    total_duration: float | None = None
    warnings: list[str] = field(default_factory=list)
