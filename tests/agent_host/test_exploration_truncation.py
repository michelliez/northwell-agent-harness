"""A cut-off search and a finished one must not look the same in the trace.

`exploration_node` had three exits -- the model stopping on its own, the round
cap, and the chunk cap -- and all three emitted the same `exploration.completed`
with the same chunk count. A run that never looked up the tables it needed was
indistinguishable from one that found everything, which is how a confident
answer got written over a single table document.
"""

from __future__ import annotations

import json

import pytest

from agent_host.nodes.exploration_nodes import _TRUNCATING_STOPS
from agent_host.trace_contract import EVENT_SPEC, domain_for


def _events(trace_path) -> list[dict]:
    return [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]


# --- the contract knows the event ---------------------------------------------


def test_truncated_is_registered_and_is_not_an_ok_status() -> None:
    spec = EVENT_SPEC["exploration.truncated"]
    assert spec.domain == "retrieval"
    assert spec.status == "blocked", "a cut-off search must not read as a normal step"


def test_truncated_is_downstream_work() -> None:
    """Retrieval really ran, so graders must count it as downstream."""
    assert EVENT_SPEC["exploration.truncated"].downstream is True
    assert domain_for("exploration.truncated") == "retrieval"


# --- the stop reasons are the ones the loop can produce -----------------------


def test_truncating_stops_are_budget_ceilings_only() -> None:
    """`model_finished` is a complete search and must never be truncation."""
    assert set(_TRUNCATING_STOPS) == {"max_rounds", "max_retrieved_chunks"}
    assert "model_finished" not in _TRUNCATING_STOPS
    assert "no_tools" not in _TRUNCATING_STOPS


@pytest.mark.parametrize("reason", sorted(_TRUNCATING_STOPS))
def test_each_truncating_reason_names_a_real_budget_field(reason: str) -> None:
    from agent_host.budget import ExecutionBudget

    assert hasattr(ExecutionBudget(), reason)


# --- the node emits it --------------------------------------------------------


def test_chunk_cap_records_truncation_and_a_stop_reason(tmp_path, monkeypatch) -> None:
    trace_path = _run_exploration_hitting_chunk_cap(tmp_path, monkeypatch)
    events = _events(trace_path)
    names = [e["event"] for e in events]

    assert "exploration.truncated" in names, (
        "the chunk cap ended the search with tables still unread and said nothing"
    )
    truncated = next(e for e in events if e["event"] == "exploration.truncated")
    assert truncated["reason"] == "max_retrieved_chunks"

    completed = next(e for e in events if e["event"] == "exploration.completed")
    assert completed["stop_reason"] == "max_retrieved_chunks"


def test_model_finishing_on_its_own_is_not_truncation(tmp_path, monkeypatch) -> None:
    trace_path = _run_exploration_model_stops(tmp_path, monkeypatch)
    events = _events(trace_path)

    assert "exploration.truncated" not in [e["event"] for e in events]
    completed = next(e for e in events if e["event"] == "exploration.completed")
    assert completed["stop_reason"] == "model_finished"


# --- state shape ---------------------------------------------------------------


def test_tool_chunks_are_normalized_to_the_client_shape(tmp_path, monkeypatch) -> None:
    """Tool results carry the index's raw `doc_id`; graph state must carry the
    retrieval client's `document_id`, or context_gate's snapshot builder dies
    on the exact chunks a successful exploration produced."""
    import agent_host.nodes.exploration_nodes as mod
    from agent_host.nodes.exploration_nodes import exploration_node

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(mod, "tools_for_intent", lambda _intent: [{"name": "find_table_doc"}])
    _install_fake_client(monkeypatch, [_Block("find_table_doc", {"table": "PAT_ENC_HSP"})])

    def _execute(_intent, _name, _args, *, db_path, budget):  # noqa: ARG001
        # The shape fetch_chunk_by_id actually returns: doc_id, not document_id.
        return {
            "chunks": [
                {
                    "chunk_id": f"c{i}",
                    "doc_id": f"d{i}",
                    "title": "PAT_ENC_HSP - Clarity Dictionary",
                    "heading_path": "PAT_ENC_HSP",
                    "category": "metadata",
                    "source_path": "PAT_ENC_HSP.html",
                    "text": "hospital encounters",
                    "rank": i + 1,
                }
                for i in range(50)
            ]
        }

    monkeypatch.setattr(mod, "execute_retrieval_tool", _execute)

    result = exploration_node(
        {
            "question": "count hospital encounters by admission date",
            "intent": "safe_sql_generation",
            "run_id": "run",
            "trace_file": str(tmp_path / "run.jsonl"),
        }
    )

    chunks = result["retrieved_chunks"]
    assert chunks, "exploration collected chunks"
    for chunk in chunks:
        assert chunk["document_id"] == chunk["doc_id"]

    # The exact construction context_gate performs must not raise.
    from retrieval.client import RetrievedChunk

    RetrievedChunk(
        chunk_id=chunks[0]["chunk_id"],
        document_id=chunks[0]["document_id"],
        source_path=chunks[0]["source_path"],
        title=chunks[0].get("title"),
        heading_path=chunks[0].get("heading_path"),
        category=chunks[0].get("category"),
        text=chunks[0]["text"],
        rank=chunks[0].get("rank", 1),
        score=chunks[0].get("score"),
    )


def test_assistant_echo_strips_gateway_extras() -> None:
    """Response blocks can carry response-only extras (the AI Hub Vertex path
    adds `parsed_output` to text blocks); echoing them back is a 400."""
    from anthropic.types import TextBlock, ToolUseBlock

    from agent_host.nodes.exploration_nodes import _assistant_content

    text = TextBlock.model_construct(
        type="text", text="Looking up PAT_ENC_HSP.", parsed_output={"unexpected": True}
    )
    tool_use = ToolUseBlock(
        id="tu_1", name="find_table_doc", input={"table": "PAT_ENC_HSP"}, type="tool_use"
    )
    empty = TextBlock.model_construct(type="text", text="")

    content = _assistant_content([text, tool_use, empty])

    assert content == [
        {"type": "text", "text": "Looking up PAT_ENC_HSP."},
        {
            "type": "tool_use",
            "id": "tu_1",
            "name": "find_table_doc",
            "input": {"table": "PAT_ENC_HSP"},
        },
    ]


# --- harness ------------------------------------------------------------------


def _Block(name: str, tool_input: dict):
    """A real `ToolUseBlock`; the node filters on isinstance, so a stub is skipped."""
    from anthropic.types import ToolUseBlock

    return ToolUseBlock(id="tu_1", name=name, input=tool_input, type="tool_use")


def _install_fake_client(monkeypatch, blocks: list) -> None:
    import agent_host.nodes.exploration_nodes as mod

    class _Messages:
        def create(self, **_kwargs):
            class _Response:
                content = blocks

            return _Response()

    class _FakeAnthropic:
        def __init__(self, **_kwargs) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(mod, "Anthropic", _FakeAnthropic)


def _install_fake_tool(monkeypatch, chunk_count: int) -> None:
    import agent_host.nodes.exploration_nodes as mod

    def _execute(_intent, _name, _args, *, db_path, budget):  # noqa: ARG001
        return {
            "chunks": [
                {"chunk_id": f"c{i}", "text": "encounters table", "doc_title": "PAT_ENC"}
                for i in range(chunk_count)
            ]
        }

    monkeypatch.setattr(mod, "execute_retrieval_tool", _execute)


def _prepare(tmp_path, monkeypatch, blocks, chunk_count):
    import agent_host.nodes.exploration_nodes as mod
    from agent_host.nodes.exploration_nodes import exploration_node

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(mod, "tools_for_intent", lambda _intent: [{"name": "find_table_doc"}])
    _install_fake_client(monkeypatch, blocks)
    _install_fake_tool(monkeypatch, chunk_count)

    trace_path = tmp_path / "run.jsonl"
    exploration_node(
        {
            "question": "which tables cover appointment volume by department",
            "intent": "table_discovery",
            "run_id": "run",
            "trace_file": str(trace_path),
        }
    )
    return trace_path


def _run_exploration_hitting_chunk_cap(tmp_path, monkeypatch):
    # One round returns more chunks than the budget allows, tripping the cap.
    return _prepare(
        tmp_path,
        monkeypatch,
        [_Block("find_table_doc", {"table": "PAT_ENC"})],
        chunk_count=50,
    )


def _run_exploration_model_stops(tmp_path, monkeypatch):
    # No tool blocks at all: the model decided it was done.
    return _prepare(tmp_path, monkeypatch, [], chunk_count=0)
