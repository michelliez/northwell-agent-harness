from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_host.config import Settings
from agent_host.mcp_process_manager import (
    MCPServerManager,
    MCPServerSpec,
    _local_endpoint,
    services_for_intent,
)


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def settings(tmp_path: Path) -> Settings:
    return Settings(
        anthropic_api_key=None,
        anthropic_base_url=None,
        mcp_log_dir=str(tmp_path / "mcp-logs"),
    )


def test_services_for_intent_starts_only_required_servers() -> None:
    assert services_for_intent("general_question") == ()
    assert services_for_intent("documentation_lookup") == ("rag",)
    assert services_for_intent("schema_lookup") == ("rag",)
    assert services_for_intent("safe_sql_generation") == (
        "catalog",
        "sql_generation",
        "sql_validation",
    )


def test_auto_start_rejects_remote_and_nonstandard_endpoints() -> None:
    with pytest.raises(RuntimeError, match="local HTTP"):
        _local_endpoint(MCPServerSpec("intent", "example", "https://example.com/mcp", 8002))

    with pytest.raises(RuntimeError, match="port 8002"):
        _local_endpoint(MCPServerSpec("intent", "example", "http://localhost:9000/mcp", 8002))


@pytest.mark.asyncio
async def test_manager_reuses_existing_server_without_owning_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    started = False

    async def reachable(host: str, port: int) -> bool:
        assert (host, port) == ("localhost", 8002)
        return True

    async def create_process(*args: Any, **kwargs: Any) -> FakeProcess:
        nonlocal started
        started = True
        return FakeProcess()

    monkeypatch.setattr("agent_host.mcp_process_manager._is_reachable", reachable)
    monkeypatch.setattr(
        "agent_host.mcp_process_manager.asyncio.create_subprocess_exec",
        create_process,
    )

    manager = MCPServerManager(settings(tmp_path))
    await manager.ensure_started(["intent"])
    await manager.stop()

    assert not started


@pytest.mark.asyncio
async def test_manager_starts_and_stops_owned_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = FakeProcess()
    reachability = iter([False, True])

    async def reachable(host: str, port: int) -> bool:
        return next(reachability)

    async def create_process(*args: Any, **kwargs: Any) -> FakeProcess:
        assert args[1:3] == ("-m", "mcp_servers.intent")
        assert kwargs["stderr"] is not None
        return process

    monkeypatch.setattr("agent_host.mcp_process_manager._is_reachable", reachable)
    monkeypatch.setattr(
        "agent_host.mcp_process_manager.asyncio.create_subprocess_exec",
        create_process,
    )

    async with MCPServerManager(settings(tmp_path)) as manager:
        await manager.ensure_started(["intent"])
        assert not process.terminated

    assert process.terminated
    assert not process.killed
    assert (tmp_path / "mcp-logs" / "intent.log").exists()
