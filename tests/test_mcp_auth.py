from __future__ import annotations

import pytest

from mcp_servers.auth import StaticServiceTokenAuth


@pytest.mark.asyncio
async def test_mcp_service_auth_rejects_missing_or_wrong_tokens() -> None:
    auth = StaticServiceTokenAuth(service="catalog", token="local-secret")

    assert await auth.verify_token("") is None
    assert await auth.verify_token("wrong-secret") is None


@pytest.mark.asyncio
async def test_mcp_service_auth_returns_scoped_host_identity() -> None:
    auth = StaticServiceTokenAuth(service="catalog", token="local-secret")

    token = await auth.verify_token("local-secret")

    assert token is not None
    assert token.client_id == "agent-harness-host"
    assert token.subject == "agent-harness-host"
    assert token.scopes == ["mcp:catalog"]
    assert token.claims["service"] == "catalog"


@pytest.mark.asyncio
async def test_missing_server_token_denies_every_request() -> None:
    auth = StaticServiceTokenAuth(service="catalog", token=None)

    assert await auth.verify_token("anything") is None
