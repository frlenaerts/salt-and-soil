from __future__ import annotations
import logging
import httpx
from .dtos import MountResponse, StatusResponse, ListDirsResponse

log = logging.getLogger("salt-and-soil.api_client")


class AgentError(RuntimeError):
    """An agent call failed. The message is always human-readable — httpx
    timeouts stringify to '', which used to surface as a bare '⚠' in the log."""


class AgentUnreachableError(AgentError):
    """The request never reached the agent (connect refused or timed out)."""


class AgentTimeoutError(AgentError):
    """The agent accepted the connection but did not answer in time."""


class AgentAPIClient:
    def __init__(self, base_url: str, api_key: str = "", name: str = ""):
        self.base_url = base_url.rstrip("/")
        self.headers  = {"X-Api-Key": api_key} if api_key else {}
        self.name     = name or self.base_url

    def _client(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self.headers, timeout=timeout)

    async def _request(self, method: str, path: str, *, timeout: float = 30, **kwargs) -> httpx.Response:
        """Send one request; translate httpx failures into AgentError subclasses
        whose message says what went wrong and where to look."""
        who  = f"[{self.name}] {method} {path}"
        url  = f"{self.base_url}{path}"
        try:
            async with self._client(timeout) as c:
                r = await c.request(method, url, **kwargs)
                r.raise_for_status()
                return r
        except httpx.ConnectTimeout as e:
            raise AgentUnreachableError(
                f"{who}: no connection to {self.base_url} within {timeout:g}s — "
                f"agent host unreachable (is the VPN/Tailscale link up on both nodes?)"
            ) from e
        except httpx.ConnectError as e:
            raise AgentUnreachableError(
                f"{who}: cannot connect to {self.base_url} ({e}) — "
                f"is the agent service running and listening on that port?"
            ) from e
        except httpx.TimeoutException as e:
            raise AgentTimeoutError(
                f"{who}: no answer within {timeout:g}s — agent is reachable but did "
                f"not respond in time (check the agent log, e.g. a hanging NFS mount)"
            ) from e
        except httpx.HTTPStatusError as e:
            raise AgentError(
                f"{who}: HTTP {e.response.status_code} — {_error_detail(e.response)}"
            ) from e
        except httpx.HTTPError as e:
            raise AgentError(f"{who}: {type(e).__name__}: {str(e) or 'request failed'}") from e

    async def mount(self, alias: str) -> MountResponse:
        r = await self._request("POST", "/mount", json={"alias": alias})
        return MountResponse(**r.json())

    async def unmount(self, alias: str | None = None) -> MountResponse:
        """Unmount one source by alias, or all currently-mounted shares if alias is None."""
        payload = {"alias": alias} if alias else {}
        r = await self._request("POST", "/unmount", json=payload)
        return MountResponse(**r.json())

    async def status(self) -> StatusResponse:
        r    = await self._request("GET", "/status", timeout=10)
        data = r.json()
        return StatusResponse(
            ok        = data.get("ok", False),
            node_name = data.get("node_name", ""),
            mounts    = data.get("mounts", []),
            error     = data.get("error", ""),
        )

    async def list_dirs(self, alias: str) -> ListDirsResponse:
        r = await self._request("GET", "/list", timeout=120, params={"alias": alias})
        return ListDirsResponse.from_dict(r.json())

    async def health(self) -> bool:
        try:
            async with self._client(timeout=5) as c:
                r = await c.get(f"{self.base_url}/health")
                return r.status_code == 200
        except Exception as e:
            log.debug("Health check failed for %s: %s", self.base_url, e)
            return False


def _error_detail(r: httpx.Response) -> str:
    """FastAPI puts the reason in {"detail": ...}; fall back to the reason phrase."""
    try:
        detail = r.json().get("detail")
    except Exception:
        detail = None
    return str(detail) if detail else (r.reason_phrase or "error")
