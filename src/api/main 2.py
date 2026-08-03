"""FastAPI application for the SQL agent backend."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent_host.config import get_config
from agent_host.graph import build_graph
from sql.audit_log import AuditLog

from .models import HealthResponse
from .routes import ask, audit

# Initialize FastAPI app
app = FastAPI(
    title="SQL Agent API",
    description="Governed SQL query execution for healthcare data",
    version="0.1.0",
)

# CORS for local development and Streamlit
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:8501").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    """Initialize graph and audit log on startup."""
    config = get_config()
    graph = build_graph()
    audit_log = AuditLog(Path(".local/audit_logs"))

    # Store in app state for route handlers
    app.state.config = config
    app.state.workflow = graph
    app.state.audit_log = audit_log


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="ok")


# Include routers
app.include_router(ask.router, prefix="/api/v1", tags=["queries"])
app.include_router(audit.router, prefix="/api/v1", tags=["audit"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("API_PORT", "8000")),
        log_level="info",
    )
