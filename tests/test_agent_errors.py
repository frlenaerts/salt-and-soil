"""
test_agent_errors.py — agent call failures must produce a readable log line
and must not leave shares mounted.

Regression: an httpx timeout stringifies to '', so a hanging or unreachable
agent used to show up as a bare '⚠' and left the local NAS mounted.
"""
import asyncio
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
import pytest

from salt_and_soil.config.loader import load
from salt_and_soil.mounts.models import MountInfo
from salt_and_soil.roles.orchestrator import OrchestratorRuntime
from salt_and_soil.shared.enums import AppStatus, MountStatus
from salt_and_soil.transport.api_client import (
    AgentAPIClient, AgentError, AgentTimeoutError, AgentUnreachableError,
)
from salt_and_soil.transport.dtos import StatusResponse


# ── AgentAPIClient error translation ──────────────────────────────────────────

def _client_with(handler) -> AgentAPIClient:
    client = AgentAPIClient("http://10.0.0.5:8081", name="agent-01")
    client._client = lambda timeout=30: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=timeout,
    )
    return client


def _raises(exc):
    def handler(request):
        raise exc
    return handler


@pytest.mark.parametrize("exc, expected_type, expected_text", [
    (httpx.ConnectTimeout(""),             AgentUnreachableError, "no connection to http://10.0.0.5:8081"),
    (httpx.ConnectError("Connection refused"), AgentUnreachableError, "cannot connect"),
    (httpx.ReadTimeout(""),                AgentTimeoutError,     "no answer within 30s"),
])
async def test_transport_errors_get_a_readable_message(exc, expected_type, expected_text):
    client = _client_with(_raises(exc))
    with pytest.raises(expected_type) as ei:
        await client.mount("music")
    msg = str(ei.value)
    assert msg.startswith("[agent-01] POST /mount: ")
    assert expected_text in msg


async def test_http_error_includes_fastapi_detail():
    client = _client_with(lambda r: httpx.Response(401, json={"detail": "Invalid or missing X-Api-Key"}))
    with pytest.raises(AgentError) as ei:
        await client.mount("music")
    assert "HTTP 401" in str(ei.value)
    assert "Invalid or missing X-Api-Key" in str(ei.value)


async def test_real_hanging_agent_raises_timeout_with_message(monkeypatch):
    """End-to-end with a real socket: the agent accepts, then never answers."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")

    async def never_answer(reader, writer):
        await asyncio.sleep(5)

    server = await asyncio.start_server(never_answer, "127.0.0.1", 0)
    port   = server.sockets[0].getsockname()[1]
    client = AgentAPIClient(f"http://127.0.0.1:{port}", name="agent-01")
    try:
        with pytest.raises(AgentTimeoutError) as ei:
            await client._request("POST", "/mount", timeout=0.2, json={"alias": "music"})
        assert "no answer within 0.2s" in str(ei.value)
    finally:
        server.close()


# ── Orchestrator cleanup after a failed agent mount ───────────────────────────

ORCHESTRATOR_TOML = textwrap.dedent("""
    [app]
    role      = "orchestrator"
    node_name = "orchestrator-01"

    [mount_defaults]
    mount_root_local  = "{root}/mnt"
    mount_root_remote = "{root}/mnt"

    [state]
    state_file   = "{root}/state/state.json"
    snapshot_dir = "{root}/state/snapshots"

    [[agents]]
    name = "agent-01"
    host = "10.0.0.5"
    port = 8081

    [[sources]]
    alias        = "music"
    agent        = "agent-01"
    local_host   = "192.168.1.99"
    local_share  = "/volume1/music"
    remote_share = "/volume1/music"
""")


class FakeNFS:
    def __init__(self, share: str, mount_point: Path):
        self.host        = "192.168.1.99"
        self.share       = share
        self.mount_point = str(mount_point)
        self.mounted     = False

    async def mount(self) -> MountInfo:
        self.mounted = True
        return MountInfo(
            remote_host=self.host, remote_share=self.share, local_path=self.mount_point,
            status=MountStatus.MOUNTED, writable=True, total_bytes=1, free_bytes=1,
        )

    async def is_mounted(self) -> bool:
        return self.mounted

    async def unmount(self) -> bool:
        self.mounted = False
        return True


class FakeAgent:
    def __init__(self, mount_exc: Exception):
        self.mount_exc = mount_exc
        self.unmounted: list[str | None] = []

    async def mount(self, alias):
        raise self.mount_exc

    async def unmount(self, alias=None):
        self.unmounted.append(alias)

    async def status(self):
        return StatusResponse(ok=True, node_name="agent-01", mounts=[])


@pytest.fixture
def runtime(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(ORCHESTRATOR_TOML.format(root=tmp_path))
    rt = OrchestratorRuntime(load(cfg_file))

    mp = tmp_path / "mnt" / "music"
    mp.mkdir(parents=True)
    (mp / "album").mkdir()   # non-empty, so the empty-mount guard passes
    nfs = FakeNFS("/volume1/music", mp)
    rt.registry._mounts[("192.168.1.99", "/volume1/music")] = nfs
    return rt, nfs


def _install_agent(rt, agent):
    rt.agent_clients["agent-01"] = agent


async def test_agent_timeout_logs_reason_and_cleans_up_both_sides(runtime):
    rt, nfs = runtime
    agent = FakeAgent(AgentTimeoutError("[agent-01] POST /mount: no answer within 30s"))
    _install_agent(rt, agent)

    await rt.run_scan()

    assert rt.status == AppStatus.ERROR
    assert "no answer within 30s" in rt._error
    assert any("⚠ [agent-01] POST /mount: no answer" in line for line in rt._log)
    assert nfs.mounted is False, "local share must be released after a failed agent mount"
    assert agent.unmounted == ["music"], "agent got the /mount, so it must get an /unmount"


async def test_unreachable_agent_skips_remote_unmount(runtime):
    rt, nfs = runtime
    agent = FakeAgent(AgentUnreachableError("[agent-01] POST /mount: no connection"))
    _install_agent(rt, agent)

    await rt.run_scan()

    assert rt.status == AppStatus.ERROR
    assert nfs.mounted is False
    assert agent.unmounted == [], "request never reached the agent — nothing to unmount there"


async def test_exception_with_empty_message_still_logs_something(runtime):
    rt, _ = runtime
    _install_agent(rt, FakeAgent(httpx.ReadTimeout("")))

    await rt.run_scan()

    assert rt._error == "ReadTimeout"
    assert not any(line.endswith("⚠ ") for line in rt._log)
