from anthropic.types import TextBlock, ToolUseBlock

from harness_spike.agent_host.agent import assistant_content


def test_assistant_content_replays_only_api_fields() -> None:
    blocks = [
        TextBlock(type="text", text="I'll look that up."),
        ToolUseBlock(
            type="tool_use",
            id="tool-1",
            name="search_tables",
            input={"question": "appointments"},
        ),
    ]

    assert assistant_content(blocks) == [
        {"type": "text", "text": "I'll look that up."},
        {
            "type": "tool_use",
            "id": "tool-1",
            "name": "search_tables",
            "input": {"question": "appointments"},
        },
    ]
