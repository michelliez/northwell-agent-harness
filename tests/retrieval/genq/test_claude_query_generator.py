from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from retrieval.genq.claude_query_generator import (
    ClaudeHaikuQueryGenerator,
    _messages_url,
)


class FakeMessages:
    def __init__(self, query_batches: list[list[Any]]) -> None:
        self.query_batches = query_batches
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        queries = self.query_batches.pop(0)
        block = SimpleNamespace(type="tool_use", name="return_queries", input={"queries": queries})
        return SimpleNamespace(content=[block])


class FakeClient:
    def __init__(self, query_batches: list[list[Any]]) -> None:
        self.messages = FakeMessages(query_batches)


def test_claude_generates_one_structured_result_per_passage() -> None:
    client = FakeClient([["question a", "question b"], ["question c", "question d"]])
    generator = ClaudeHaikuQueryGenerator("test-haiku", client=client)

    result = generator.generate(
        ["first passage", "second passage"],
        queries_per_passage=2,
        max_input_tokens=300,
        max_query_tokens=64,
        top_p=0.9,
        seed=42,
    )

    assert result == [["question a", "question b"], ["question c", "question d"]]
    assert len(client.messages.calls) == 2
    assert client.messages.calls[0]["model"] == "test-haiku"
    assert client.messages.calls[0]["tool_choice"]["name"] == "return_queries"
    schema = client.messages.calls[0]["tools"][0]["input_schema"]["properties"]["queries"]
    assert schema["minItems"] == 2
    assert schema["maxItems"] == 2


def test_claude_passage_is_bounded_by_approximate_input_tokens() -> None:
    client = FakeClient([["question"]])
    generator = ClaudeHaikuQueryGenerator(client=client)

    generator.generate(
        ["x" * 100],
        queries_per_passage=1,
        max_input_tokens=5,
        max_query_tokens=10,
        top_p=1.0,
        seed=0,
    )

    content = client.messages.calls[0]["messages"][0]["content"]
    assert content.endswith("x" * 20)
    assert "x" * 21 not in content


@pytest.mark.parametrize("queries", [["only one"], ["one", 2]])
def test_claude_rejects_wrong_or_non_string_query_values(queries: list[Any]) -> None:
    generator = ClaudeHaikuQueryGenerator(client=FakeClient([queries]))

    with pytest.raises(RuntimeError, match="expected 2"):
        generator.generate(
            ["passage"],
            queries_per_passage=2,
            max_input_tokens=300,
            max_query_tokens=64,
            top_p=0.95,
            seed=42,
        )


def test_claude_requires_tool_result() -> None:
    client = FakeClient([["unused"]])
    client.messages.create = lambda **_kwargs: SimpleNamespace(content=[])  # type: ignore[method-assign]
    generator = ClaudeHaikuQueryGenerator(client=client)

    with pytest.raises(RuntimeError, match="required return_queries"):
        generator.generate(
            ["passage"],
            queries_per_passage=1,
            max_input_tokens=300,
            max_query_tokens=64,
            top_p=0.95,
            seed=42,
        )


def test_claude_accepts_raw_http_response_mapping() -> None:
    class DictMessages:
        def create(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "return_queries",
                        "input": {"queries": ["question a", "question b"]},
                    }
                ]
            }

    client = SimpleNamespace(messages=DictMessages())
    generator = ClaudeHaikuQueryGenerator(client=client)

    result = generator.generate(
        ["passage"],
        queries_per_passage=2,
        max_input_tokens=300,
        max_query_tokens=64,
        top_p=0.95,
        seed=42,
    )

    assert result == [["question a", "question b"]]


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.anthropic.com", "https://api.anthropic.com/v1/messages"),
        ("https://example.test/v1", "https://example.test/v1/messages"),
        ("https://example.test/v1/messages", "https://example.test/v1/messages"),
    ],
)
def test_messages_url_normalizes_supported_base_urls(base_url: str, expected: str) -> None:
    assert _messages_url(base_url) == expected
