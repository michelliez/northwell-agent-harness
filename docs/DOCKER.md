# Docker and Compose Setup

This document describes how to build and run the Clarity Agent application in Docker containers locally and prepare it for deployment.

## Architecture

The application runs as two services in separate containers:

- **API Service** (`:8000`): FastAPI backend handling policy checks, retrieval, SQL planning, and execution
- **UI Service** (`:8501`): Chainlit frontend for user interaction via web browser

Both services are built from a single Docker image to ensure consistency.

## Local Development with Docker Compose

### Prerequisites

- Docker Engine 20.10+ (or Docker Desktop)
- Docker Compose 2.0+
- Unix-like shell (macOS, Linux) or PowerShell on Windows

### Build the Image

```bash
docker build -t clarity-agent:dev .
```

### Run with Compose

The default `compose.yaml` mounts the RAG index from the shared `fixtures/`
directory beside this repository:

```bash
docker compose up
```

This will start three services:
1. **API** (`http://localhost:8000`) - FastAPI backend
2. **Chat UI** (`http://localhost:8501`) - Chainlit chat interface
3. **Metrics Dashboard** (`http://localhost:8502`) - Streamlit analytics (auto-detects dark/light mode)

You can now:
- Open `http://localhost:8501` to chat with the agent
- Open `http://localhost:8502` to view metrics and analytics

**To disable the metrics dashboard** (optional), edit `compose.yaml` and comment out the `metrics` service.

### Access the Services

| Service | URL | Purpose |
|---------|-----|---------|
| **Chat** | `http://localhost:8501` | Ask questions, interact with the agent |
| **Metrics** | `http://localhost:8502` | View performance, cost, and quality analytics |
| **API** | `http://localhost:8000` | Backend API (for direct API calls) |

### Stop the Services

```bash
docker compose down
```

To remove volumes (including mounted artifact data):

```bash
docker compose down -v
```

## Configuration

### Environment Variables

Configure the services via environment variables in `compose.yaml` or pass a local environment file explicitly:

```bash
# .env.local (do not commit)
ANTHROPIC_API_KEY=<your-key>
ALLOWED_ORIGINS=http://localhost:8501
```

```bash
docker compose --env-file .env.local up
```

**Important**: Never commit `.env` files containing secrets. Use `compose.yaml` or a separate `.env.local` that is gitignored.

### Common Variables

| Variable | Default | Used By | Purpose |
|----------|---------|---------|---------|
| `ANTHROPIC_API_KEY` | (required) | API | Claude API key for LLM |
| `AI_HUB_API_KEY` | (none) | API | Alternative to `ANTHROPIC_API_KEY` for AI Hub |
| `RAG_DB_PATH` | `/opt/clarity/rag/index.sqlite` | API | Path to SQLite RAG index in container |
| `DENSE_INDEX_DIR` | `/opt/clarity/dense` | API | Path to FAISS embeddings in container |
| `ARTIFACT_PATH` | `/var/lib/clarity` | API | Base path for audit logs, traces, and outputs |
| `ALLOWED_ORIGINS` | `http://localhost:8501` | API | CORS allowed origins (comma-separated) |
| `AGENT_API_URL` | `http://api:8000` | UI | FastAPI backend URL accessible from Chainlit |

### Mounting Custom RAG Indexes

By default, Compose mounts the shared runtime RAG index. To use a different index:

1. Set the volume mount in `compose.yaml`:

```yaml
api:
  volumes:
    - /path/to/your/index.sqlite:/opt/clarity/rag/index.sqlite:ro
```

Or use a docker run override:

```bash
docker compose run -v /path/to/your/index.sqlite:/opt/clarity/rag/index.sqlite:ro api
```

### Mounting FAISS Dense Index (Optional)

If you have a FAISS dense index available:

```yaml
api:
  volumes:
    - /path/to/dense/dir:/opt/clarity/dense:ro
```

The application will gracefully fall back to FTS-only retrieval if the dense index is not available.

## Health Checks

### API Service

- **`/health`** (lightweight liveness check)
  ```bash
  curl http://localhost:8000/health
  ```

- **`/ready`** (detailed readiness check)
  ```bash
  curl http://localhost:8000/ready
  ```

The readiness endpoint verifies:
- RAG SQLite index exists and is accessible
- AI provider API key is configured
- Dense FAISS artifacts exist (if `DENSE_INDEX_DIR` is set)

### UI Service

The Chainlit service is considered ready once it can reach the API:

```bash
curl http://localhost:8501/
```

## Persistence

By default, Compose creates a named volume `clarity_artifacts` for audit logs and traces:

```yaml
volumes:
  clarity_artifacts:
    driver: local
```

To access logs written by the containers:

```bash
# View volume contents
docker volume inspect clarity_artifacts

# Copy audit logs to local directory
docker run --rm -v clarity_artifacts:/artifacts alpine cp -r /artifacts/audit_logs ./
```

To use a bind mount instead (for easier local access):

```yaml
api:
  volumes:
    - ./artifacts:/var/lib/clarity
```

Then audit logs will appear in `./artifacts/audit_logs/`.

## Building for Production

### Without Secrets

Never bake secrets into the image:

```bash
docker build -t clarity-agent:v1.0.0 .
```

### Using BuildKit for Better Caching

```bash
DOCKER_BUILDKIT=1 docker build -t clarity-agent:v1.0.0 .
```

### Building Multiple Architectures

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t clarity-agent:v1.0.0 .
```

## Testing

### Container Smoke Tests

Run the container test suite to verify the image is properly configured:

```bash
uv run pytest tests/containers/ -v
```

These tests check:
- Image builds successfully
- No secrets or local artifacts in image layers
- Services start with proper health checks
- Non-root user execution
- API and UI services can communicate

### Manual Testing

After starting with `docker compose up`:

1. **Test the UI loads:**
   ```bash
   curl http://localhost:8501/
   ```

2. **Test API readiness:**
   ```bash
   curl http://localhost:8000/ready
   ```

3. **Test a safe query via API:**
   ```bash
   curl -X POST http://localhost:8000/api/v1/ask \
     -H "Content-Type: application/json" \
     -d '{"text":"What tables are available?","thread_id":"test-1"}'
   ```

4. **Test the Chainlit frontend:**
   Open `http://localhost:8501` and type a question

## Troubleshooting

### "docker: command not found"

Install Docker Desktop (macOS/Windows) or Docker Engine (Linux).

### "docker compose: unknown flag: -d"

Update Docker Compose to v2.0+:

```bash
docker compose version  # Should be v2.0 or later
```

### Services fail to start

Check logs:

```bash
docker compose logs -f api
docker compose logs -f ui
```

### API says "RAG index not found"

Ensure the RAG index file exists at the mounted path. By default, it should be at `../fixtures/rag/index-v6-genq-combined-expansion.sqlite`. If the fixture doesn't exist, rebuild it or mount a different index.

### Chainlit can't reach API

Verify that:
1. API service is running: `docker compose ps api`
2. API is healthy: `docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"`
3. UI can resolve and reach the API: `docker compose exec ui python -c "import urllib.request; print(urllib.request.urlopen('http://api:8000/health').read().decode())"`

### "ANTHROPIC_API_KEY not found"

Set it in your environment before starting Compose:

```bash
export ANTHROPIC_API_KEY=<your-key>
docker compose up
```

Or add it to `.env.local`:

```
ANTHROPIC_API_KEY=<your-key>
```

## Next Steps

Once the local Docker setup works:

1. **CI/CD**: Add GitHub Actions workflows to build and push images
2. **Image Registry**: Push to container registry (Docker Hub, ECR, etc.)
3. **Staging Deployment**: Deploy to Northwell's staging environment
4. **Load Testing**: Test with production-scale concurrent users
5. **Security Scanning**: Run Trivy or similar tools on the image
6. **Authentication**: Add OIDC or other auth in front of Chainlit and API
7. **Observability**: Integrate with logging, metrics, and tracing systems

## References

- [FastAPI Deployment with Docker](https://fastapi.tiangolo.com/deployment/docker/)
- [Chainlit Deployment Guide](https://docs.chainlit.io/deploy/overview)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [Docker Best Practices](https://docs.docker.com/develop/dev-best-practices/)
- [uv Docker Integration](https://docs.astral.sh/uv/guides/integration/docker/)
