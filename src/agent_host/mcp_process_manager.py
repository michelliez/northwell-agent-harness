from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO
from urllib.parse import urlparse

from agent_host.config import Settings


@dataclass(frozen=True)
class MCPServerSpec:
    name: str
    module: str
    url: str
    port: int


@dataclass
class _OwnedProcess:
    process: asyncio.subprocess.Process
    log_file: IO[bytes]


class MCPServerManager:
    """Start local HTTP MCP servers for one host invocation.

    Existing listeners are reused and never terminated. Only subprocesses
    started by this manager are stopped when its context exits.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._specs = {
            "catalog": MCPServerSpec(
                "catalog", "mcp_servers.data_catalog", settings.mcp_server_url, 8000
            ),
            "intent": MCPServerSpec("intent", "mcp_servers.intent", settings.intent_mcp_url, 8002),
            "sql_generation": MCPServerSpec(
                "sql_generation",
                "mcp_servers.sql_generation",
                settings.sql_generation_mcp_url,
                8003,
            ),
            "sql_validation": MCPServerSpec(
                "sql_validation",
                "mcp_servers.sql_validation",
                settings.sql_validation_mcp_url,
                8004,
            ),
            "rag": MCPServerSpec("rag", "retrieval.mcp_server", settings.rag_mcp_url, 8005),
        }
        self._owned: dict[str, _OwnedProcess] = {}

    async def __aenter__(self) -> MCPServerManager:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.stop()

    async def ensure_started(self, names: list[str] | tuple[str, ...]) -> None:
        for name in names:
            if name in self._owned:
                continue
            try:
                spec = self._specs[name]
            except KeyError as exc:
                raise ValueError(f"Unknown MCP server: {name}") from exc
            await self._ensure_server(spec)

    async def stop(self) -> None:
        owned = list(reversed(self._owned.values()))
        self._owned.clear()

        for item in owned:
            if item.process.returncode is None:
                item.process.terminate()

        for item in owned:
            if item.process.returncode is None:
                try:
                    await asyncio.wait_for(item.process.wait(), timeout=3.0)
                except TimeoutError:
                    item.process.kill()
                    await item.process.wait()
            item.log_file.close()

    async def _ensure_server(self, spec: MCPServerSpec) -> None:
        host, port = _local_endpoint(spec)
        if await _is_reachable(host, port):
            return

        log_dir = Path(self._settings.mcp_log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = (log_dir / f"{spec.name}.log").open("ab")
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                spec.module,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
            )
        except BaseException:
            log_file.close()
            raise

        self._owned[spec.name] = _OwnedProcess(process=process, log_file=log_file)
        deadline = time.monotonic() + self._settings.mcp_startup_timeout_seconds
        while time.monotonic() < deadline:
            if process.returncode is not None:
                raise RuntimeError(
                    f"MCP server {spec.name!r} exited during startup; see {log_file.name}"
                )
            if await _is_reachable(host, port):
                return
            await asyncio.sleep(0.05)

        raise RuntimeError(f"Timed out starting MCP server {spec.name!r}; see {log_file.name}")


def services_for_intent(intent: str) -> tuple[str, ...]:
    if intent in {
        "documentation_lookup",
        "table_discovery",
        "schema_lookup",
        "aggregate_definition",
    }:
        return ("rag",)
    if intent == "safe_sql_generation":
        return ("catalog", "sql_generation", "sql_validation")
    return ()


def _local_endpoint(spec: MCPServerSpec) -> tuple[str, int]:
    parsed = urlparse(spec.url)
    host = parsed.hostname
    port = parsed.port
    if parsed.scheme not in {"http", "https"} or host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(
            f"MCP auto-start only supports local HTTP URLs; configure {spec.name!r} "
            "as a managed service or set MCP_AUTO_START=false."
        )
    if port is None or port != spec.port:
        raise RuntimeError(f"MCP auto-start expects {spec.name!r} on port {spec.port}, not {port}.")
    return host, port


async def _is_reachable(host: str, port: int) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=0.25,
        )
    except TimeoutError, OSError:
        return False
    writer.close()
    await writer.wait_closed()
    return True
