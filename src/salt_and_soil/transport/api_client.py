from __future__ import annotations
import logging
import httpx
from .dtos import MountResponse, StatusResponse, ListDirsResponse

log = logging.getLogger("salt-and-soil.api_client")


class AgentAPIClient:
    def __init__(self, base_url: str, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.headers  = {"X-Api-Key": api_key} if api_key else {}

    def _client(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self.headers, timeout=timeout)

    async def mount(self, alias: str) -> MountResponse:
        async with self._client() as c:
            r = await c.post(f"{self.base_url}/mount", json={"alias": alias})
            r.raise_for_status()
            return MountResponse(**r.json())

    async def unmount(self, alias: str | None = None) -> MountResponse:
        """Unmount one source by alias, or all currently-mounted shares if alias is None."""
        payload = {"alias": alias} if alias else {}
        async with self._client() as c:
            r = await c.post(f"{self.base_url}/unmount", json=payload)
            r.raise_for_status()
            return MountResponse(**r.json())

    async def status(self) -> StatusResponse:
        async with self._client(timeout=10) as c:
            r = await c.get(f"{self.base_url}/status")
            r.raise_for_status()
            data = r.json()
            return StatusResponse(
                ok        = data.get("ok", False),
                node_name = data.get("node_name", ""),
                mounts    = data.get("mounts", []),
                error     = data.get("error", ""),
            )

    async def list_dirs(self, alias: str) -> ListDirsResponse:
        async with self._client(timeout=120) as c:
            r = await c.get(f"{self.base_url}/list", params={"alias": alias})
            r.raise_for_status()
            return ListDirsResponse.from_dict(r.json())

    async def health(self) -> bool:
        try:
            async with self._client(timeout=5) as c:
                r = await c.get(f"{self.base_url}/health")
                return r.status_code == 200
        except Exception as e:
            log.debug("Health check failed for %s: %s", self.base_url, e)
            return False
