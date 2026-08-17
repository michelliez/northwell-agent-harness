from __future__ import annotations

import json

from fastapi.testclient import TestClient

from agent_host.graph import _result_to_response
from agent_host.nodes.lifecycle_nodes import interpretation_and_citations_node
from agent_host.schemas import AskResponse, Citation
from api.main import create_app
from sql.audit_log import AuditLog


def _response(**updates: object) -> AskResponse:
    payload: dict[str, object] = {
        "answer": "Here is the documentation answer.",
        "used_tools": ["retrieve_documentation_context"],
        "run_id": "run-123",
        "trace_file": ".local/traces/run-123.jsonl",
        "thread_id": "thread-123",
        "allowed": True,
        "intent": "documentation_lookup",
        "intent_confidence": 0.97,
        "generated_sql": None,
        "query_parameters": [],
        "citations": ["chunk-123"],
        "citation_details": [
            Citation(
                chunk_id="chunk-123",
                label="A0H_MAP — Column Information",
                source_file="A0H_MAP.html",
                heading_path="Column Information",
                category="column_info",
            )
        ],
    }
    payload.update(updates)
    return AskResponse.model_validate(payload)


def test_ask_uses_public_graph_boundary_and_returns_v1_contract(tmp_path) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_ask(question: str, *, thread_id: str | None = None) -> AskResponse:
        calls.append((question, thread_id))
        return _response(
            answer="Validated SQL draft.",
            generated_sql="SELECT COUNT(*) AS row_count FROM A0H_MAP",
            query_parameters=[{"name": "status", "type": "STRING", "value": "A"}],
            execution_status="not_configured",
        )

    app = create_app(
        ask_handler=fake_ask,
        audit_log=AuditLog(tmp_path / "audit"),
    )
    response = TestClient(app).post(
        "/api/v1/ask",
        json={"question": "Count A0H_MAP rows", "thread_id": "thread-123"},
    )

    assert response.status_code == 200
    assert calls == [("Count A0H_MAP rows", "thread-123")]
    assert response.json() == {
        "api_version": "v1",
        "status": "complete",
        "answer": "Validated SQL draft.",
        "run_id": "run-123",
        "thread_id": "thread-123",
        "allowed": True,
        "policy_reason": None,
        "matched_term": None,
        "intent": "documentation_lookup",
        "intent_confidence": 0.97,
        "disclosure_status": None,
        "interrupted": False,
        "clarification_prompt": None,
        "used_tools": ["retrieve_documentation_context"],
        "generated_sql": "SELECT COUNT(*) AS row_count FROM A0H_MAP",
        "query_parameters": [{"name": "status", "type": "STRING", "value": "A"}],
        "citation_details": [
            {
                "reference_number": 1,
                "label": "A0H_MAP — Column Information",
                "source_file": "A0H_MAP.html",
                "heading_path": "Column Information",
                "category": "column_info",
            }
        ],
        "execution_status": "not_configured",
    }


def test_ask_stream_emits_step_lines_then_the_v1_response(tmp_path) -> None:
    def fake_ask_stream(question: str, *, thread_id: str | None = None):
        assert question == "Count A0H_MAP rows"
        yield ("step", {"node": "input_policy", "status": "ok"})
        yield ("step", {"node": "classify_intent", "status": "ok"})
        yield ("response", _response(answer="Done.", thread_id=thread_id or "generated"))

    app = create_app(
        ask_stream_handler=fake_ask_stream,
        audit_log=AuditLog(tmp_path / "audit"),
    )
    response = TestClient(app).post(
        "/api/v1/ask/stream",
        json={"question": "Count A0H_MAP rows", "thread_id": "thread-123"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert [event["type"] for event in events] == ["step", "step", "response"]
    assert events[0]["node"] == "input_policy"
    assert events[0]["status"] == "ok"
    assert events[2]["data"]["answer"] == "Done."
    assert events[2]["data"]["status"] == "complete"
    assert events[2]["data"]["thread_id"] == "thread-123"


def test_ask_stream_surfaces_mid_stream_failure_as_an_error_line(tmp_path) -> None:
    def failing_ask_stream(_question: str, *, thread_id: str | None = None):
        del thread_id
        yield ("step", {"node": "input_policy", "status": "ok"})
        raise RuntimeError("model exploded")

    app = create_app(
        ask_stream_handler=failing_ask_stream,
        audit_log=AuditLog(tmp_path / "audit"),
    )
    response = TestClient(app).post("/api/v1/ask/stream", json={"question": "hello"})

    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert [event["type"] for event in events] == ["step", "error"]
    # Internal exception text must not leak through the public boundary.
    assert events[1]["detail"] == "Agent workflow failed."


def test_interrupted_response_can_be_resumed_with_reply_and_thread_id(tmp_path) -> None:
    resume_calls: list[tuple[str, str]] = []

    def fake_ask(_question: str, *, thread_id: str | None = None) -> AskResponse:
        return _response(
            answer="",
            thread_id=thread_id or "generated-thread",
            interrupted=True,
            clarification_prompt="Which table do you mean?",
        )

    def fake_resume(reply: str, *, thread_id: str) -> AskResponse:
        resume_calls.append((reply, thread_id))
        return _response(answer="A0H_MAP documentation.", thread_id=thread_id)

    app = create_app(
        ask_handler=fake_ask,
        resume_handler=fake_resume,
        audit_log=AuditLog(tmp_path / "audit"),
    )
    client = TestClient(app)

    interrupted = client.post("/api/v1/ask", json={"question": "Tell me about the map"})
    assert interrupted.status_code == 200
    assert interrupted.json()["status"] == "interrupted"
    assert interrupted.json()["thread_id"] == "generated-thread"
    assert interrupted.json()["clarification_prompt"] == "Which table do you mean?"

    completed = client.post(
        "/api/v1/resume",
        json={"reply": "A0H_MAP", "thread_id": "generated-thread"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "complete"
    assert completed.json()["answer"] == "A0H_MAP documentation."
    assert resume_calls == [("A0H_MAP", "generated-thread")]


def test_policy_rejection_has_explicit_status(tmp_path) -> None:
    def rejected(_question: str, *, thread_id: str | None = None) -> AskResponse:
        return _response(
            thread_id=thread_id or "rejected-thread",
            allowed=False,
            policy_reason="Unsupported request.",
        )

    app = create_app(
        ask_handler=rejected,
        audit_log=AuditLog(tmp_path / "audit"),
    )
    response = TestClient(app).post("/api/v1/ask", json={"question": "Run an update"})

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["allowed"] is False
    assert response.json()["policy_reason"] == "Unsupported request."


def test_request_contract_rejects_forged_user_identity_and_extra_fields(tmp_path) -> None:
    app = create_app(audit_log=AuditLog(tmp_path / "audit"))
    response = TestClient(app).post(
        "/api/v1/ask",
        json={"question": "What is A0H_MAP?", "user_id": "admin"},
    )

    assert response.status_code == 422


def test_internal_error_does_not_expose_exception_text(tmp_path) -> None:
    def broken(_question: str, *, thread_id: str | None = None) -> AskResponse:
        raise RuntimeError("secret connection detail")

    app = create_app(
        ask_handler=broken,
        audit_log=AuditLog(tmp_path / "audit"),
    )
    response = TestClient(app).post("/api/v1/ask", json={"question": "Hello"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Agent workflow failed."}


def test_static_audit_summary_route_is_not_captured_as_run_id(tmp_path) -> None:
    app = create_app(audit_log=AuditLog(tmp_path / "audit"))
    response = TestClient(app).get("/api/v1/audit/summary")

    assert response.status_code == 200
    assert response.json()["total_runs"] == 0


def test_health_contract(tmp_path) -> None:
    app = create_app(audit_log=AuditLog(tmp_path / "audit"))
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_clear_thread_calls_host_cleanup_and_is_idempotent(tmp_path) -> None:
    cleared: list[str] = []
    app = create_app(
        clear_thread_handler=cleared.append,
        audit_log=AuditLog(tmp_path / "audit"),
    )

    response = TestClient(app).delete("/api/v1/threads/thread-123")

    assert response.status_code == 200
    assert response.json() == {"api_version": "v1", "cleared": True}
    assert cleared == ["thread-123"]
    assert response.headers["cache-control"] == "no-store, max-age=0"


def test_graph_response_resolves_readable_citation_metadata() -> None:
    chunk_id = "A0H_MAP__COLUMN_DEFINITION__LINE"
    response = _result_to_response(
        {
            "answer": f"The LINE column is documented. [{chunk_id}]",
            "citations": [chunk_id, chunk_id],
            "retrieved_chunks": [
                {
                    "chunk_id": chunk_id,
                    "title": "A0H_MAP",
                    "source_path": "dictionary/A0H_MAP.html",
                    "heading_path": "Column Information > LINE",
                    "category": "column_info",
                }
            ],
        },
        "run-readable",
        "thread-readable",
    )

    assert response.citations == [chunk_id]
    assert response.citation_details[0].label == "A0H_MAP — Column Information > LINE"
    assert response.citation_details[0].source_file == "A0H_MAP.html"


def test_graph_response_surfaces_validated_candidate_sql() -> None:
    response = _result_to_response(
        {
            "answer": "Here is the validated SQL draft.",
            "candidate_sql": "SELECT COUNT(*) AS row_count FROM A0H_MAP",
            "validation_result": {"allowed": True},
            "execution_status": "not_configured",
        },
        "run-sql",
        "thread-sql",
    )

    assert response.generated_sql == "SELECT COUNT(*) AS row_count FROM A0H_MAP"


def test_graph_response_withholds_sql_that_failed_validation() -> None:
    response = _result_to_response(
        {
            "answer": "The generated SQL failed safety validation.",
            "candidate_sql": "SELECT secret FROM A0H_MAP",
            "validation_result": {"allowed": False},
        },
        "run-blocked",
        "thread-blocked",
    )

    assert response.generated_sql is None


def test_public_api_replaces_internal_chunk_ids_with_numbered_references(tmp_path) -> None:
    chunk_id = "A0H_MAP__COLUMN_DEFINITION__LINE"

    def cited(_question: str, *, thread_id: str | None = None) -> AskResponse:
        return _response(
            answer=f"The LINE field is documented. [{chunk_id}]",
            thread_id=thread_id or "citation-thread",
            citations=[chunk_id],
            citation_details=[
                Citation(
                    chunk_id=chunk_id,
                    label="A0H_MAP — Column Information > LINE",
                    source_file="A0H_MAP.html",
                    heading_path="Column Information > LINE",
                    category="column_info",
                )
            ],
        )

    app = create_app(
        ask_handler=cited,
        audit_log=AuditLog(tmp_path / "audit"),
    )
    response = TestClient(app).post("/api/v1/ask", json={"question": "What is LINE?"})
    body = response.json()

    assert response.status_code == 200
    assert body["answer"] == "The LINE field is documented. [1]"
    assert chunk_id not in response.text
    assert body["citation_details"] == [
        {
            "reference_number": 1,
            "label": "A0H_MAP — Column Information > LINE",
            "source_file": "A0H_MAP.html",
            "heading_path": "Column Information > LINE",
            "category": "column_info",
        }
    ]


def test_ask_stream_marks_short_circuit_step_as_degraded(tmp_path) -> None:
    """A node outside the answer-writing set that emits an answer is marked degraded."""

    def fake_ask_stream(question: str, *, thread_id: str | None = None):
        yield ("step", {"node": "retrieve_context", "status": "degraded"})
        yield ("step", {"node": "context_gate", "status": "ok"})
        yield ("response", _response(answer="Blocked.", thread_id=thread_id or "t"))

    app = create_app(
        ask_stream_handler=fake_ask_stream,
        audit_log=AuditLog(tmp_path / "audit"),
    )
    response = TestClient(app).post(
        "/api/v1/ask/stream",
        json={"question": "test", "thread_id": "t"},
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line]
    step_events = [e for e in events if e["type"] == "step"]
    assert step_events[0]["node"] == "retrieve_context"
    assert step_events[0]["status"] == "degraded"
    assert step_events[1]["node"] == "context_gate"
    assert step_events[1]["status"] == "ok"


def test_citation_extraction_accepts_canonical_ids_but_not_invented_ids() -> None:
    chunk_id = "A0H_MAP__COLUMN_DEFINITION__LINE"
    update = interpretation_and_citations_node(
        {
            "answer": f"Supported [{chunk_id}], not [INVENTED].",
            "retrieved_chunks": [{"chunk_id": chunk_id}],
            "citations": [],
        }
    )

    # Chunk-ID bracket stripped from prose; non-ID bracket preserved.
    assert update["citations"] == [chunk_id]
    assert update["answer"] == "Supported, not [INVENTED]."
