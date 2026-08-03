# FastAPI adapter

This package exposes the existing LangGraph public API over HTTP. It does not
construct graph state itself and does not authorize or execute BigQuery.

## Run locally

```bash
uv run uvicorn api.main:app --app-dir src --reload --reload-dir src --port 8000
```

Use one worker while LangGraph checkpoints and bounded thread context remain in
memory.

## Conversation continuity and retention

Send the `thread_id` returned by one completed `/ask` response with the next
`/ask`. The server can then resolve an explicit reference such as "this table"
to the latest unique table anchor. Both the original prompt and the resolved
standalone prompt pass the deterministic input policy screen.

The Streamlit client retains only the opaque `thread_id`. The API process keeps
at most two recent standalone questions and catalog anchors for 30 minutes; it
does not retain assistant answers, retrieved passages, SQL, parameters, or
query results as conversation context. Completed LangGraph checkpoints are
deleted. This process-local compromise requires one API worker and is not a
replacement for an authenticated, encrypted shared store.

## Contract

### `POST /api/v1/ask`

```json
{
  "question": "What is the A0H_MAP table?",
  "thread_id": null
}
```

### `POST /api/v1/resume`

This endpoint resumes a LangGraph clarification interrupt. It is not a cost or
execution approval endpoint.

```json
{
  "reply": "I mean the A0H_MAP Clarity table.",
  "thread_id": "thread-from-ask"
}
```

### `DELETE /api/v1/threads/{thread_id}`

Idempotently clears bounded follow-up metadata and any pending clarification
checkpoint. The Streamlit **Clear** button calls this endpoint before clearing
its local thread ID.

Both endpoints return the same versioned response:

```json
{
  "api_version": "v1",
  "status": "complete",
  "answer": "...",
  "run_id": "...",
  "thread_id": "...",
  "allowed": true,
  "policy_reason": null,
  "matched_term": null,
  "intent": "documentation_lookup",
  "intent_confidence": 0.97,
  "disclosure_status": null,
  "interrupted": false,
  "clarification_prompt": null,
  "used_tools": ["retrieve_documentation_context"],
  "generated_sql": null,
  "query_parameters": [],
  "citation_details": [
    {
      "reference_number": 1,
      "label": "A0H_MAP — Column Information",
      "source_file": "A0H_MAP.html",
      "heading_path": "Column Information",
      "category": "column_info"
    }
  ],
  "execution_status": null
}
```

The answer uses numbered references such as `[1]`. Internal chunk IDs remain in
LangGraph state and privacy-aware traces, but are not sent in this public API
response. Clients should display the reference number and readable label.

`status` is one of `complete`, `interrupted`, or `rejected`. Operational
failures use non-2xx HTTP responses and do not expose exception details.

Client-supplied `user_id` is deliberately rejected. When authentication is
implemented, the server must derive identity and roles from a verified
principal rather than JSON supplied by the browser.

## Other endpoints

- `GET /health`
- `GET /api/v1/audit/summary`
- `GET /api/v1/audit/{run_id}`

The audit endpoints expose the separate SQL audit log. The graph's privacy-aware
trace is not returned as part of the ask response.

## Test

```bash
uv run pytest tests/api -q
```
