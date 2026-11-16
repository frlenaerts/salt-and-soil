"""
test_agent_input_validation.py — what an agent sends back is data, not truth.

Folder names from /list and mount points from /status end up in rsync and
`rm -rf` paths. A broken, compromised or spoofed agent must not be able to
point those at anything but a direct child of a configured source.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
import pytest

from salt_and_soil.shared.enums import JobStatus, SyncAction
from salt_and_soil.shared.paths import is_plain_name
from salt_and_soil.state.models import SyncJob
from salt_and_soil.sync.executor import ResolvedSource, SyncExecutor
from salt_and_soil.transport.api_client import AgentAPIClient, AgentError
from salt_and_soil.transport.dtos import DirEntry, ListDirsResponse, StatusResponse

from test_agent_errors import FakeNFS, ORCHESTRATOR_TOML
from salt_and_soil.config.loader import load
from salt_and_soil.roles.orchestrator import OrchestratorRuntime
from salt_and_soil.shared.enums import AppStatus

BAD_NAMES  = ["", ".", "..", "../x", "a/b", "/etc", "x/", "a\x00b", None, 7, ["x"]]
GOOD_NAMES = ["Movie A", "Été 2024", "-rf", "..hidden", "a..b", "back\\slash", "#recycle", "$(reboot)", "*"]


# ── names ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", BAD_NAMES)
def test_bad_names_are_not_plain(name):
    assert is_plain_name(name) is False


@pytest.mark.parametrize("name", GOOD_NAMES)
def test_ordinary_names_are_plain(name):
    assert is_plain_name(name) is True


@pytest.mark.parametrize("name", BAD_NAMES)
def test_list_response_rejects_bad_folder_names(name):
    with pytest.raises(ValueError, match="not a plain directory name"):
        ListDirsResponse.from_dict({"source_alias": "videos", "dirs": [{"name": name, "size_bytes": 1}]})


@pytest.mark.parametrize("size", [-1, "12", 1.5, None, True])
def test_list_response_rejects_bad_sizes(size):
    with pytest.raises(ValueError, match="non-negative integer"):
        DirEntry.from_dict({"name": "ok", "size_bytes": size})


@pytest.mark.parametrize("payload", [None, [], "x", {"dirs": []}, {"source_alias": "v", "dirs": {}}, {"source_alias": "v", "dirs": ["x"]}])
def test_list_response_rejects_malformed_payloads(payload):
    with pytest.raises(ValueError):
        ListDirsResponse.from_dict(payload)


def test_list_response_accepts_valid_entries_and_ignores_unknown_keys():
    resp = ListDirsResponse.from_dict({
        "source_alias": "videos",
        "dirs": [{"name": n, "size_bytes": 0, "extra": "ignored"} for n in GOOD_NAMES],
        "future_field": 1,
    })
    assert [d.name for d in resp.dirs] == GOOD_NAMES


def test_dir_entry_roundtrip():
    e = DirEntry(name="Movie A", size_bytes=42)
    assert DirEntry.from_dict(e.to_dict()) == e


# ── AgentAPIClient.list_dirs ──────────────────────────────────────────────────

def _client_with(handler) -> AgentAPIClient:
    client = AgentAPIClient("http://10.0.0.5:8081", name="agent-01")
    client._client = lambda timeout=30: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=timeout,
    )
    return client


@pytest.mark.parametrize("body, expected", [
    ({"source_alias": "videos", "dirs": [{"name": "..", "size_bytes": 1}]}, "folder name '..' is not a plain directory name"),
    ({"source_alias": "videos", "dirs": [{"name": "", "size_bytes": 1}]},   "folder name '' is not a plain directory name"),
    ({"source_alias": "other",  "dirs": []},                                "asked for 'videos', got another source"),
])
async def test_list_dirs_turns_bad_payloads_into_readable_agent_errors(body, expected):
    client = _client_with(lambda r: httpx.Response(200, json=body))
    with pytest.raises(AgentError) as ei:
        await client.list_dirs("videos")
    assert str(ei.value).startswith("[agent-01] GET /list: invalid response — ")
    assert expected in str(ei.value)


async def test_list_dirs_rejects_non_json_body():
    client = _client_with(lambda r: httpx.Response(200, text="<html>not json</html>"))
    with pytest.raises(AgentError, match="invalid response"):
        await client.list_dirs("videos")


# ── SyncExecutor ──────────────────────────────────────────────────────────────

class _Proc:
    returncode = 0

    class stdout:           # noqa: N801 — mimics asyncio's attribute
        @staticmethod
        async def read(n):
            return b""

    async def wait(self):
        return 0


@pytest.fixture
def spawned(monkeypatch):
    """Record subprocess invocations instead of running rsync/ssh."""
    calls: list[list[str]] = []

    async def fake_exec(*cmd, **kwargs):
        calls.append(list(cmd))
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


def _executor() -> SyncExecutor:
    return SyncExecutor(
        source_map   = {"videos": ResolvedSource("/mnt/local/videos", "/mnt/remote/videos")},
        remote_host  = "agent.example",
        remote_user  = "root",
        ssh_key_file = "/root/.ssh/key",
    )


async def _run(job: SyncJob) -> list[str]:
    return [line async for line in _executor().execute(job)]


@pytest.mark.parametrize("action", [SyncAction.SYNC, SyncAction.PULL, SyncAction.DELETE_REMOTE])
@pytest.mark.parametrize("folder", ["", ".", "..", "../../etc", "a/b"])
async def test_executor_refuses_unsafe_folder_names(spawned, action, folder):
    job = SyncJob(job_id="1", source_alias="videos", folder=folder, action=action)
    lines = await _run(job)

    assert spawned == [], "no rsync/ssh may be started for an unsafe folder name"
    assert job.status == JobStatus.FAILED
    assert "not a plain directory name" in job.error
    assert any(line.startswith("ERROR:") for line in lines)


async def test_executor_delete_targets_only_the_child_folder(spawned):
    job = SyncJob(job_id="1", source_alias="videos", folder="Old Show", action=SyncAction.DELETE_REMOTE)
    await _run(job)

    assert len(spawned) == 1
    assert spawned[0][0] == "ssh"
    assert spawned[0][-1] == "rm -rf -- '/mnt/remote/videos/Old Show'"
    assert job.status == JobStatus.DONE


async def test_executor_rsync_paths_stay_inside_the_source(spawned):
    job = SyncJob(job_id="1", source_alias="videos", folder="-rf", action=SyncAction.PULL)
    await _run(job)

    assert spawned[0][0] == "rsync"
    assert spawned[0][-2:] == ["root@agent.example:/mnt/remote/videos/-rf/", "/mnt/local/videos/-rf/"]


# ── mount points from /status ─────────────────────────────────────────────────

@pytest.fixture
def runtime(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(ORCHESTRATOR_TOML.format(root=tmp_path).replace(
        f'mount_root_remote = "{tmp_path}/mnt"', 'mount_root_remote = "/mnt/salt-and-soil"'))
    rt = OrchestratorRuntime(load(cfg_file))
    mp = tmp_path / "mnt" / "music"
    mp.mkdir(parents=True)
    (mp / "album").mkdir()
    rt.registry._mounts[("192.168.1.99", "/volume1/music")] = FakeNFS("/volume1/music", mp)
    return rt


@pytest.mark.parametrize("value", [
    "/mnt/salt-and-soil/nas_volume1-music",
    "/mnt/salt-and-soil/nas_volume1-music/",
])
def test_mount_point_below_the_remote_root_is_accepted(runtime, value):
    assert runtime._checked_remote_mount_point("agent-01", "music", value) == "/mnt/salt-and-soil/nas_volume1-music"


@pytest.mark.parametrize("value", [
    "/", "/etc", "/mnt/salt-and-soil", "/mnt/salt-and-soil/", "/mnt/salt-and-soil-evil/x",
    "/mnt/salt-and-soil/../../etc", "/mnt/salt-and-soil/x/..", "mnt/salt-and-soil/x", "", None, 5,
])
def test_mount_point_outside_the_remote_root_is_rejected(runtime, value):
    with pytest.raises(RuntimeError, match="not below mount_root_remote"):
        runtime._checked_remote_mount_point("agent-01", "music", value)


class _Agent:
    def __init__(self, mounts):
        self.mounts = mounts

    async def mount(self, alias):
        from salt_and_soil.transport.dtos import MountResponse
        return MountResponse(ok=True, mounted=True)

    async def unmount(self, alias=None):
        pass

    async def status(self):
        return StatusResponse(ok=True, node_name="agent-01", mounts=self.mounts)


async def test_scan_fails_when_agent_reports_a_mount_point_outside_the_root(runtime):
    runtime.agent_clients["agent-01"] = _Agent([{"alias": "music", "mount_point": "/"}])
    await runtime.run_scan()

    assert runtime.status == AppStatus.ERROR
    assert "not below mount_root_remote" in runtime._error


async def test_status_entries_for_foreign_or_malformed_aliases_are_ignored(runtime):
    runtime.agent_clients["agent-01"] = _Agent([
        {"alias": "not-configured", "mount_point": "/"},
        {"alias": ["music"], "mount_point": "/"},
        "garbage",
        {"alias": "music", "mount_point": "/mnt/salt-and-soil/nas_music"},
    ])
    assert await runtime._do_mount_all() == {"music": "/mnt/salt-and-soil/nas_music"}
