from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value

    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}

    if isinstance(value, list | tuple | set):
        return [jsonable(item) for item in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return jsonable(model_dump(mode="json", exclude_none=True))

    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        return jsonable(dict_method())

    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


class TraceLogger:
    def __init__(self, trace_dir: str = "logs/runs", run_id: str | None = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex
        self.path = Path(trace_dir) / f"{self.run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **fields: Any) -> None:
        row = {
            "ts": time.time(),
            "run_id": self.run_id,
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(jsonable(row), ensure_ascii=True) + "\n")
