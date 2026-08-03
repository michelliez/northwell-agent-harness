from __future__ import annotations

import httpx


class AgentAPIError(RuntimeError):
    """A safe error that the Streamlit UI may display."""


class AgentAPIClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        timeout_seconds: float = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def health(self) -> dict:
        return self._get("/health")

    def ask(
        self,
        question: str,
        thread_id: str | None = None,
    ) -> dict:
        payload: dict[str, str] = {
            "question": question,
        }

        if thread_id:
            payload["thread_id"] = thread_id

        return self._post("/api/v1/ask", payload)

    def resume(
        self,
        reply: str,
        thread_id: str,
    ) -> dict:
        return self._post(
            "/api/v1/resume",
            {
                "reply": reply,
                "thread_id": thread_id,
            },
        )

    def clear_thread(self, thread_id: str) -> dict:
        return self._request("DELETE", f"/api/v1/threads/{thread_id}")

    def _get(self, path: str) -> dict:
        return self._request("GET", path)

    def _request(self, method: str, path: str) -> dict:
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError as exc:
            raise AgentAPIError(
                "Could not connect to FastAPI. Make sure it is running on port 8000."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise AgentAPIError(self._error_message(exc.response)) from exc
        except httpx.HTTPError as exc:
            raise AgentAPIError("The API request failed.") from exc

    def _post(
        self,
        path: str,
        payload: dict,
    ) -> dict:
        try:
            response = httpx.post(
                f"{self.base_url}{path}",
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError as exc:
            raise AgentAPIError(
                "Could not connect to FastAPI. Make sure it is running on port 8000."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise AgentAPIError(self._error_message(exc.response)) from exc
        except httpx.HTTPError as exc:
            raise AgentAPIError("The API request failed.") from exc

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            body = response.json()
            detail = body.get("detail")
            if isinstance(detail, str):
                return detail
        except ValueError:
            pass

        return f"API request failed with status {response.status_code}."


class AsyncAgentAPIClient:
    """Async client used by chat frontends such as Chainlit."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        timeout_seconds: float = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def ask(self, question: str, thread_id: str | None = None) -> dict:
        payload: dict[str, str] = {"question": question}
        if thread_id:
            payload["thread_id"] = thread_id
        return await self._request("POST", "/api/v1/ask", json=payload)

    async def resume(self, reply: str, thread_id: str) -> dict:
        return await self._request(
            "POST",
            "/api/v1/resume",
            json={"reply": reply, "thread_id": thread_id},
        )

    async def clear_thread(self, thread_id: str) -> dict:
        return await self._request("DELETE", f"/api/v1/threads/{thread_id}")

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                return response.json()
        except httpx.ConnectError as exc:
            raise AgentAPIError(
                "Could not connect to FastAPI. Make sure it is running on port 8000."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise AgentAPIError(AgentAPIClient._error_message(exc.response)) from exc
        except httpx.HTTPError as exc:
            raise AgentAPIError("The API request failed.") from exc
