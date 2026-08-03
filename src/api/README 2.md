# FastAPI Backend

HTTP API for the SQL agent. Wraps the LangGraph workflow and provides:
- `/api/v1/ask` - Submit questions
- `/api/v1/resume` - Approve expensive queries
- `/api/v1/audit/{run_id}` - Get audit trail for a run
- `/api/v1/audit/summary` - Get summary of all queries

## Running Locally

```bash
# Development (auto-reload on code changes)
python -m uvicorn src.api.main:app --reload --port 8000

# Production
python -m uvicorn src.api.main:app --port 8000 --workers 4
```

## Environment Variables

```bash
# Required
ANTHROPIC_API_KEY=<your-key>
GOOGLE_CLOUD_PROJECT=<your-project>

# Optional
API_PORT=8000
BIGQUERY_LOCATION=us-west1
ALLOWED_ORIGINS=http://localhost:8501,https://app.example.com
```

## API Endpoints

### POST /api/v1/ask
Submit a question.

**Request:**
```json
{
  "question": "Count appointments by department",
  "user_id": "jane.smith"
}
```

**Response:**
```json
{
  "run_id": "uuid-here",
  "events": [
    {
      "timestamp": "2026-07-31T12:00:00Z",
      "event_type": "policy_check",
      "decision": "allowed",
      "metadata": {}
    },
    ...
  ],
  "status": "complete",
  "result": [
    {"DEPT_ID": "CARDIOLOGY", "count": 42},
    {"DEPT_ID": "ONCOLOGY", "count": 15}
  ]
}
```

### POST /api/v1/resume
Resume execution with approval token (for expensive queries).

**Request:**
```json
{
  "run_id": "uuid-here",
  "approval_token": "v1.1722515820.5000000000.base64_signature"
}
```

**Response:** Same as /ask

### GET /api/v1/audit/{run_id}
Get audit trail for a run.

**Response:**
```json
{
  "run_id": "uuid-here",
  "events": [
    {
      "timestamp": "2026-07-31T12:00:00Z",
      "run_id": "uuid-here",
      "user_id": "jane.smith",
      "event_type": "policy_check",
      "decision": "allowed",
      "reason": null,
      "sql": null,
      "bytes_processed": null
    }
  ],
  "summary": {
    "policy_check": {"allowed": 1, "rejected": 0, "error": 0},
    "intent_classified": {"allowed": 1, "rejected": 0, "error": 0}
  }
}
```

### GET /api/v1/audit/summary
Get summary of all audited queries.

**Query Parameters:**
- `start_date` (optional): ISO 8601 timestamp (e.g., "2026-07-30T00:00:00Z")
- `end_date` (optional): ISO 8601 timestamp
- `user_id` (optional): Filter by user

**Response:**
```json
{
  "total_runs": 5,
  "runs": {
    "run-uuid-1": {
      "timestamp": "2026-07-31T12:00:00Z",
      "user_id": "jane.smith",
      "events": {
        "policy_check": "allowed",
        "intent_classified": "allowed",
        "execution": "allowed"
      }
    }
  },
  "filters": {
    "start_date": "2026-07-30T00:00:00Z",
    "end_date": null,
    "user_id": "jane.smith"
  }
}
```

## Deployment

### Docker

```dockerfile
FROM python:3.14-slim
RUN pip install uv
WORKDIR /app
COPY . .
RUN uv sync
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Docker Compose

```yaml
version: '3.8'
services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      GOOGLE_CLOUD_PROJECT: ${GOOGLE_CLOUD_PROJECT}
      ALLOWED_ORIGINS: http://localhost:8501
```

## Testing

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ --cov=src

# Test the API specifically
uv run pytest tests/test_api/ -v
```

## Integration with Streamlit

Streamlit calls the API instead of the workflow directly:

```python
import requests

API_URL = "http://localhost:8000/api/v1"

def ask_question(question: str, user_id: str):
    response = requests.post(
        f"{API_URL}/ask",
        json={"question": question, "user_id": user_id}
    )
    return response.json()
```
