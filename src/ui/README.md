# Frontends

FastAPI remains the only browser-facing workflow boundary. Both frontends call
the versioned API and retain only its opaque thread ID in browser-session state.

## Chainlit (primary chat UI)

Start FastAPI:

```bash
uv run uvicorn api.main:app --app-dir src --reload --reload-dir src --port 8000
```

In a second terminal, start Chainlit:

```bash
uv run agent-harness-ui -w --port 8501
```

Open `http://localhost:8501`. Set `AGENT_API_URL` to use a FastAPI URL other
than `http://localhost:8000`.

Chainlit data persistence is not configured. The UI uses an in-memory user
session for the opaque API thread ID and asks FastAPI to clear bounded thread
state when the chat ends. Its visual theme is configured in
`public/chainlit.css`; edit the color variables at the top of that file to
adjust the palette without changing the workflow code.

## Streamlit (retained prototype)

The existing Streamlit implementation remains available:

```bash
uv run streamlit run src/ui/app.py --server.port 8502
```
