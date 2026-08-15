"""FastAPI application exposing the supported LangGraph public boundary."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from agent_host.config import get_config
from agent_host.graph import ask as graph_ask
from agent_host.graph import ask_stream as graph_ask_stream
from agent_host.graph import clear_thread as graph_clear_thread
from agent_host.graph import resume as graph_resume
from agent_host.schemas import AskResponse
from sql.audit_log import AuditLog

from .models import HealthResponse
from .routes import ask, audit, metrics

logger = logging.getLogger(__name__)

AskHandler = Callable[..., AskResponse]
AskStreamHandler = Callable[..., Iterator[tuple[str, dict[str, str] | AskResponse]]]
ResumeHandler = Callable[..., AskResponse]
ClearThreadHandler = Callable[[str], None]


@asynccontextmanager
async def _prewarm_dense(application: FastAPI) -> AsyncIterator[None]:
    """Load the FAISS artifact and query encoder at startup, not on the first ask."""
    dense_index_dir = get_config().dense_index_dir
    if dense_index_dir is not None:
        from retrieval.dense import load_dense_searcher

        try:
            searcher = load_dense_searcher(dense_index_dir)
            searcher.search("warm up the query encoder", 1)
            logger.info("Dense retrieval warm (%s)", searcher.metadata.model_name)
        except Exception:
            logger.exception("Dense pre-warm failed; hybrid will load on first request instead")
    yield


def create_app(
    *,
    ask_handler: AskHandler = graph_ask,
    ask_stream_handler: AskStreamHandler = graph_ask_stream,
    resume_handler: ResumeHandler = graph_resume,
    clear_thread_handler: ClearThreadHandler = graph_clear_thread,
    audit_log: AuditLog | None = None,
) -> FastAPI:
    """Build an app with injectable workflow boundaries for contract tests."""
    application = FastAPI(
        title="SQL Agent API",
        description="Policy-gated documentation and SQL-draft assistant",
        version="0.1.0",
        lifespan=_prewarm_dense,
    )
    origins = [
        value.strip()
        for value in os.getenv("ALLOWED_ORIGINS", "http://localhost:8501").split(",")
        if value.strip()
    ]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )
    application.state.ask_handler = ask_handler
    application.state.ask_stream_handler = ask_stream_handler
    application.state.resume_handler = resume_handler
    application.state.clear_thread_handler = clear_thread_handler
    artifact_path = get_config().artifact_path
    application.state.audit_log = audit_log or AuditLog(artifact_path / "audit_logs")

    @application.middleware("http")
    async def prevent_client_caching(request: Request, call_next):
        """Prevent browsers and intermediaries from retaining agent responses."""
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @application.get("/health", response_model=HealthResponse)
    async def health_check() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get("/ready", response_model=HealthResponse)
    async def readiness_check() -> HealthResponse:
        config = get_config()

        if not config.anthropic_api_key and not os.getenv("AI_HUB_API_KEY"):
            raise RuntimeError("No AI provider configured")

        index_path = config.index_path
        if not index_path.exists():
            raise RuntimeError(f"RAG index not found at {index_path}")

        if config.dense_index_dir is not None:
            if not config.dense_index_dir.exists():
                raise RuntimeError(f"Dense index dir not found at {config.dense_index_dir}")
            faiss_file = config.dense_index_dir / "corpus.faiss"
            if not faiss_file.exists():
                raise RuntimeError(f"FAISS index not found at {faiss_file}")

        return HealthResponse(status="ok")

    @application.get("/metrics", response_class=HTMLResponse)
    async def metrics_dashboard() -> str:
        """Serve the interactive metrics dashboard."""
        dashboard_path = Path(__file__).parent.parent / "ui" / "metrics_dashboard.html"
        with open(dashboard_path) as f:
            return f.read()

    application.include_router(ask.router, prefix="/api/v1", tags=["queries"])
    application.include_router(audit.router, prefix="/api/v1", tags=["audit"])
    application.include_router(metrics.router, prefix="/api/v1", tags=["metrics"])
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
        log_level="info",
    )
