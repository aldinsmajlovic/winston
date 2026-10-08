"""The way back to winstond: every core service an extension uses (Home Assistant, memory,
presence, the model, timers, nudges, entities) is one operation on winstond's extension API,
`POST /api/ext/v1/<op>` with the extension's own token. winstond checks each against the manifest."""
from __future__ import annotations

from typing import Any

import httpx

from winston_sdk.errors import HomeAssistantError, NotPermitted


class RestServices:
    def __init__(self, api: str, token: str, timeout: float = 60):
        self._client = httpx.AsyncClient(base_url=api.rstrip("/"), timeout=timeout,
                                         headers={"Authorization": f"Bearer {token}"})

    async def call(self, op: str, **params: Any) -> Any:
        try:
            r = await self._client.post(f"/api/ext/v1/{op}", json=params)
        except httpx.HTTPError as e:
            raise ConnectionError(f"winstond did not answer {op}: {e}") from e
        if r.status_code == 200:
            return r.json().get("result")
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        if r.status_code == 403:
            raise NotPermitted(detail)
        if r.status_code == 502 and op.startswith("ha."):
            raise HomeAssistantError(detail)
        raise RuntimeError(f"{op}: {detail}")


class NoServices:
    """For describing an extension: nothing is reachable, nothing should be called."""

    async def call(self, op: str, **params: Any) -> Any:
        raise RuntimeError(f"{op} is not available while the extension is being described")
