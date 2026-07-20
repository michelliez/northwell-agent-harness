from __future__ import annotations

NODE_TYPE_MAP: dict[str, str] = {
    "request.received": "input",
    "policy_gate.checked": "policy_gate",
    "request.blocked": "policy_gate",
    "intent.classification.request": "classifier",
    "intent.classification.result": "classifier",
    "intent.classification.failed": "error",
    "general_question.started": "classifier",
    "sql.workflow.started": "generation",
    "sql.workflow.completed": "generation",
    "sql.generation.refused": "generation",
    "sql.validation.blocked": "validation",
    "sql.validation.failed": "validation",
    "mcp.tools.listed": "tool_call",
    "model.request": "model_call",
    "model.response": "model_call",
    "model.request.first": "model_call",
    "model.response.first": "model_call",
    "model.request.final": "model_call",
    "model.response.final": "model_call",
    "tool.selected": "tool_call",
    "tool.result": "tool_call",
    "answer.ready": "output",
    "agent.max_rounds_reached": "error",
}

NODE_TYPE_LABELS: dict[str, str] = {
    "input": "Input",
    "policy_gate": "Policy Gate",
    "classifier": "Classifier",
    "generation": "SQL Generation",
    "validation": "Validation",
    "tool_call": "Tool Call",
    "model_call": "Model Call",
    "output": "Output",
    "error": "Error",
    "unknown": "Unknown",
}


def classify_event(event_name: str) -> str:
    return NODE_TYPE_MAP.get(event_name, "unknown")
