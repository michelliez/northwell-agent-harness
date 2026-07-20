from __future__ import annotations

import json
import hashlib
import hmac
import os
import time
import uuid
from pathlib import Path
from typing import Any, Literal


TraceContentMode = Literal["metadata", "debug"]
_SENSITIVE_KEYS = frozenset(
    {
        "question",
        "messages",
        "response",
        "input",
        "result",
        "answer",
        "sql",
        "content",
    }
)


def jsonable(value: Any) -> Any:
    """Convert SDK/Pydantic objects into plain JSON values for trace files.

    The agent does not need this to reason or call tools. This only exists
    because objects returned by SDKs are often not directly writable with
    json.dumps().
    """
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
    """Append privacy-aware JSON events to a run-specific trace file.

    Metadata mode is the safe default.  It retains event structure, sizes, and
    keyed digests while removing user, tool, SQL, and answer content.  Debug
    mode is an explicit local-development escape hatch and must not be used for
    sensitive data.
    """

    def __init__(
        self,
        trace_dir: str = "logs/runs",
        run_id: str | None = None,
        *,
        content_mode: TraceContentMode = "metadata",
        hash_key: str | None = None,
    ) -> None:
        if content_mode not in {"metadata", "debug"}:
            raise ValueError("content_mode must be 'metadata' or 'debug'")
        self.run_id = run_id or uuid.uuid4().hex
        self.path = Path(trace_dir) / f"{self.run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.content_mode = content_mode
        self._hash_key = (hash_key or os.getenv("TRACE_HASH_KEY") or self.run_id).encode(
            "utf-8"
        )

    def record(self, event: str, **fields: Any) -> None:
        row = {
            "ts": time.time(),
            "run_id": self.run_id,
            "event": event,
            **fields,
        }
        if self.content_mode == "metadata":
            row = self._redact_row(row)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(jsonable(row), ensure_ascii=True) + "\n")

    def _redact_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._redact_mapping(row)

    def _redact_mapping(self, value: dict[str, Any]) -> dict[str, Any]:
        return {
            key: self._redact_value(item)
            if key in _SENSITIVE_KEYS
            else self._redact_nested(item)
            for key, item in value.items()
        }

    def _redact_nested(self, value: Any) -> Any:
        if isinstance(value, dict):
            return self._redact_mapping(value)
        if isinstance(value, list):
            return [self._redact_nested(item) for item in value]
        return value

    def _redact_value(self, value: Any) -> dict[str, Any]:
        serialized = json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True)
        digest = hmac.new(
            self._hash_key,
            serialized.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        summary: dict[str, Any] = {
            "redacted": True,
            "bytes": len(serialized.encode("utf-8")),
            "digest": f"hmac-sha256:{digest}",
        }
        if isinstance(value, (list, tuple, set)):
            summary["item_count"] = len(value)
        return summary
