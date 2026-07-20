"""Small service-authentication provider for the local MCP POC.

The host token prevents unauthenticated direct callers from reaching a server.
It is intentionally a narrow development boundary, not a replacement for
production identity, mTLS/OAuth, or resource-level authorization.
"""

from __future__ import annotations

import hmac
import os

from dotenv import load_dotenv
from fastmcp.server.auth import AccessToken, AuthProvider


class StaticServiceTokenAuth(AuthProvider):
    """Verify one configured bearer token for one MCP service."""

    def __init__(self, *, service: str, token: str | None) -> None:
        self.service = service
        self.token = token or ""
        super().__init__(required_scopes=[f"mcp:{service}"])

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self.token or not hmac.compare_digest(token, self.token):
            return None
        return AccessToken(
            token=token,
            client_id="agent-harness-host",
            subject="agent-harness-host",
            scopes=[f"mcp:{self.service}"],
            claims={"service": self.service},
        )


def build_service_auth(service: str) -> StaticServiceTokenAuth:
    """Build a server auth provider; missing tokens deny every request."""
    load_dotenv()
    return StaticServiceTokenAuth(
        service=service,
        token=os.getenv("MCP_AUTH_TOKEN") or None,
    )


__all__ = ["StaticServiceTokenAuth", "build_service_auth"]
