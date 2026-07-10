from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from pydantic import ValidationError

from harness_spike.agent_host.agent import answer_question
from harness_spike.agent_host.schemas import AskRequest
from harness_spike.config import get_settings


class Handler(BaseHTTPRequestHandler):
    """HTTP wrapper: POST /ask in, JSON response out."""

    def do_POST(self) -> None:
        if self.path != "/ask":
            self._json({"error": "POST to /ask"}, status=404)
            return

        try:
            body = self.rfile.read(int(self.headers.get("content-length", "0")))
            request = AskRequest.model_validate_json(body)
            response = asyncio.run(answer_question(request.question))
        except (json.JSONDecodeError, KeyError, ValidationError) as exc:
            self._json({"error": str(exc)}, status=400)
            return

        self._json(response.model_dump())

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    port = get_settings().model_port
    print(f"Model server: http://localhost:{port}/ask")
    HTTPServer(("localhost", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
