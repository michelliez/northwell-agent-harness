"""Container smoke tests for Docker and Compose setup."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest


def docker_available() -> bool:
    """Check if Docker is available."""
    try:
        subprocess.run(
            ["docker", "version"],
            capture_output=True,
            check=True,
            timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def compose_available() -> bool:
    """Check if Docker Compose is available."""
    try:
        subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            check=True,
            timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


pytestmark = pytest.mark.skipif(
    not docker_available() or not compose_available(),
    reason="Docker and Docker Compose required",
)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Get repository root."""
    return Path(__file__).parent.parent.parent


def test_docker_build(repo_root: Path) -> None:
    """Test that the Docker image builds successfully."""
    result = subprocess.run(
        ["docker", "build", "-t", "clarity-agent:test", "."],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"Docker build failed: {result.stderr}"


def test_no_secrets_in_image(repo_root: Path) -> None:
    """Verify that .env and sensitive files are not in the image."""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "clarity-agent:test",
            "find",
            "/app",
            "-name",
            ".env*",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # Should return nothing (except .env.example which should be excluded by .dockerignore)
    output_lines = [
        line
        for line in result.stdout.strip().split("\n")
        if line and ".env.example" not in line
    ]
    assert not output_lines, f"Found .env files in image: {output_lines}"


def test_no_local_artifacts_in_image(repo_root: Path) -> None:
    """Verify that .local/ directory is not in the image."""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "clarity-agent:test",
            "test",
            "!",
            "-d",
            "/app/.local",
        ],
        capture_output=True,
        timeout=30,
    )
    # Return code 0 means the directory does not exist (good)
    assert result.returncode == 0, ".local/ directory found in image"


def test_image_runs_as_non_root(repo_root: Path) -> None:
    """Verify the image runs as non-root user."""
    result = subprocess.run(
        ["docker", "run", "--rm", "clarity-agent:test", "id"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    output = result.stdout.strip()
    # Should show uid=1000 (app user)
    assert "uid=1000" in output, f"Not running as app user: {output}"
    assert "uid=0" not in output, f"Running as root: {output}"


def test_venv_in_path(repo_root: Path) -> None:
    """Verify that .venv/bin is in PATH."""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "clarity-agent:test",
            "python",
            "-c",
            "import sys; print(sys.executable)",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert ".venv" in result.stdout, "venv not in PATH"


def test_api_binary_available(repo_root: Path) -> None:
    """Verify that uvicorn is available in the container."""
    result = subprocess.run(
        ["docker", "run", "--rm", "clarity-agent:test", "which", "uvicorn"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, "uvicorn not found in PATH"


def test_ui_binary_available(repo_root: Path) -> None:
    """Verify that agent-harness-ui is available in the container."""
    result = subprocess.run(
        ["docker", "run", "--rm", "clarity-agent:test", "which", "agent-harness-ui"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, "agent-harness-ui not found in PATH"


def test_compose_up_and_health(repo_root: Path) -> None:
    """Test that compose.yaml starts services and health checks work."""
    # Start services
    result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"Compose up failed: {result.stderr}"

    try:
        # Wait for API to be healthy (up to 30s)
        for _attempt in range(30):
            result = subprocess.run(
                ["docker", "compose", "ps", "--format", "json"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                services = json.loads(result.stdout)
                api_healthy = any(
                    s.get("State") == "running" and s.get("Name", "").endswith("_api_1")
                    for s in services
                )
                ui_running = any(
                    s.get("State") == "running" and s.get("Name", "").endswith("_ui_1")
                    for s in services
                )
                if api_healthy and ui_running:
                    break
            time.sleep(1)

        # Verify API is responding to /health
        result = subprocess.run(
            ["curl", "-f", "http://localhost:8000/health"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"API /health failed: {result.stderr}"

    finally:
        # Clean up
        subprocess.run(
            ["docker", "compose", "down"],
            cwd=repo_root,
            capture_output=True,
            timeout=30,
        )
