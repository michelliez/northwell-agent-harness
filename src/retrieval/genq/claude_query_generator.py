"""Claude Haiku provider for offline synthetic-query generation."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """\
You create realistic semantic-search queries for Epic Clarity data-dictionary passages.
Write questions that a healthcare data analyst, report developer, or SQL developer might ask.
Use only facts supported by the supplied passage. Do not answer the questions.
Vary wording and intent, avoid copying long phrases, and preserve important table or column names
only when a real user would plausibly include them.
"""


class MessagesClient(Protocol):
    """Subset of the Anthropic client used by this provider."""

    messages: Any


class ClaudeHaikuQueryGenerator:
    """Generate structured query lists with Claude Haiku, one passage per request."""

    device_name = "anthropic-api"

    def __init__(
        self,
        model_name: str = DEFAULT_CLAUDE_MODEL,
        *,
        client: MessagesClient | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.model_name = model_name
        if client is not None:
            self._client = client
            return

        from anthropic import Anthropic

        resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY") or os.getenv("AI_HUB_API_KEY")
        if not resolved_key:
            raise RuntimeError(
                "Claude query generation requires ANTHROPIC_API_KEY or AI_HUB_API_KEY"
            )
        resolved_base_url = base_url or os.getenv("ANTHROPIC_BASE_URL") or None
        self._client = Anthropic(
            api_key=resolved_key,
            base_url=resolved_base_url,
            default_headers=dict(default_headers or _headers_from_env()),
        )

    def generate(
        self,
        passages: Sequence[str],
        *,
        queries_per_passage: int,
        max_input_tokens: int,
        max_query_tokens: int,
        top_p: float,
        seed: int,
    ) -> list[list[str]]:
        """Generate exact-size query lists; Claude does not expose a seed parameter."""
        del seed
        results: list[list[str]] = []
        max_passage_chars = max_input_tokens * 4
        for passage in passages:
            response = self._client.messages.create(
                model=self.model_name,
                max_tokens=min(4096, max(256, queries_per_passage * max_query_tokens * 2)),
                top_p=top_p,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Generate exactly {queries_per_passage} distinct search questions "
                            "for this passage:\n\n"
                            f"{passage[:max_passage_chars]}"
                        ),
                    }
                ],
                tools=[
                    {
                        "name": "return_queries",
                        "description": "Return the generated semantic-search questions.",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "queries": {
                                    "type": "array",
                                    "items": {"type": "string", "minLength": 1},
                                    "minItems": queries_per_passage,
                                    "maxItems": queries_per_passage,
                                }
                            },
                            "required": ["queries"],
                            "additionalProperties": False,
                        },
                    }
                ],
                tool_choice={"type": "tool", "name": "return_queries"},
            )
            results.append(_extract_queries(response, queries_per_passage))
        return results


def _extract_queries(response: Any, expected_count: int) -> list[str]:
    for block in response.content:
        if getattr(block, "type", None) != "tool_use":
            continue
        if getattr(block, "name", None) != "return_queries":
            continue
        payload = getattr(block, "input", None)
        if not isinstance(payload, dict) or not isinstance(payload.get("queries"), list):
            break
        queries = payload["queries"]
        if len(queries) != expected_count or not all(isinstance(query, str) for query in queries):
            raise RuntimeError(
                f"Claude returned {len(queries)} valid query values; expected {expected_count}"
            )
        return queries
    raise RuntimeError("Claude did not return the required return_queries tool result")


def _headers_from_env() -> dict[str, str]:
    raw = os.getenv("ANTHROPIC_CUSTOM_HEADERS", "")
    headers: dict[str, str] = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        name, separator, value = item.partition(":")
        if not separator or not name.strip() or not value.strip():
            raise RuntimeError(
                "ANTHROPIC_CUSTOM_HEADERS must contain comma-separated name:value pairs"
            )
        headers[name.strip()] = value.strip()
    return headers
