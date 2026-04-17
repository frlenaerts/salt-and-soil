"""
OrchestratorRuntime manages the full sync lifecycle:
  mount → scan (local + agent) → compare → ready → sync → unmount
"""
from __future__ import annotations

import logging
from typing import Any

from ..config.models import Config
from ..mounts.nfs import NFSMount
from ..mounts.checks import assert_mount_ok, MountCheckError, is_path_empty
from ..scanner.scanner import DirScanner
from ..scanner.models import ScanSnapshot
from ..state.repository import StateRepository
from ..state.models import FolderDiff
from ..sync.comparer import compare
from ..sync.planner import build_jobs
from ..sync.executor import SyncExecutor
from ..transport.api_client import AgentAPIClient
from ..transport.dtos import ActionItem
from ..shared.enums import AppStatus, DiffStatus, SyncAction
from ..shared.clock import utc_now_iso
from ..shared.paths import human_size

log = logging.getLogger("salt-and-soil.orchestrator")


class OrchestratorRuntime:
    def __init__(self, cfg: Config):
        self.cfg    = cfg
        self.status = AppStatus.IDLE
        self._log:  list[str] = []
        self._diffs: list[FolderDiff] = []
        self._mount_info: dict | None = None
        self._error: str = ""
        self._last_scan_at: str | None = None

        # NFS mount for the local NAS
        self.nfs = NFSMount(
            host        = cfg.mount.remote_host,
            share       = cfg.mount.remote_share,
            mount_point = cfg.mount.local_mount_path,
            nfs_version = cfg.mount.nfs_version,
            nfs_options = cfg.mount.nfs_options,
        )

        # State repo
        self.repo = StateRepository(
            state_file   = cfg.state.state_file,
            snapshot_dir = cfg.state.snapshot_dir,
        )

        # Agent clients (one per configured agent)
        self.agents: list[AgentAPIClient] = [
            AgentAPIClient(
                base_url = f"http://{a.host}:{a.port}",
                api_key  = a.api_key,
            )
            for a in cfg.agents
        ]

    # ── Logging ───────────────────────────────────────────────────────────────

    @staticmethod
    def _ts() -> str:
        from datetime import datetime
        return datetime.now().strftime("%H:%M:%S")

    def _info(self, msg: str):
        log.info(msg)
        self._log.append(f"{self._ts()}  {msg}")

    def _err(self, msg: str):
        log.error(msg)
        self._log.append(f"{self._ts()}  ⚠ {msg}")

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset(self):
        self.status      = AppStatus.IDLE
        self._log        = []
        self._diffs      = []
        self._mount_info = None
        self._error      = ""

    # ── UI snapshot ───────────────────────────────────────────────────────────

    def snapshot_for_ui(self) -> dict[str, Any]:
        return {
            "status":       self.status.value,
            "log":          list(self._log),
            "diffs":        [_diff_to_dict(d) for d in self._diffs],
            "mount":        self._mount_info,
            "error":        self._error,
            "last_scan_at": self._last_scan_at,
        }

    # ── Main flow ─────────────────────────────────────────────────────────────

    async def run_scan(self):
        self.status = AppStatus.MOUNTING
        _did_mount  = False
        try:
            # 1. Mount local NAS
            self._info(f"Mounting local NAS: {self.nfs.host}:{self.nfs.share}...")
            info = await self.nfs.mount()
            _did_mount = True
            self._mount_info = {
                "source":      info.source,
                "local_path":  info.local_path,
                "status":      info.status.value,
                "writable":    info.writable,
                "total":       human_size(info.total_bytes),
                "free":        human_size(info.free_bytes),
            }
            assert_mount_ok(info)
            self._info(f"✓ Mounted — {human_size(info.total_bytes)} total, {human_size(info.free_bytes)} free")

            if is_path_empty(self.cfg.mount.local_mount_path):
                raise MountCheckError("Mount path is empty — NFS share may not be configured correctly")

            # 2. Mount agent NAS(es)
            for i, agent in enumerate(self.agents):
                agent_cfg = self.cfg.agents[i]
                self._info(f"Mounting agent '{agent_cfg.name}' via {agent_cfg.host}...")
                resp = await agent.mount()
                if not resp.ok:
                    raise RuntimeError(f"Agent mount failed: {resp.error}")
                self._info(f"✓ Agent '{agent_cfg.name}' mounted")

            # 3. Scan local
            self.status = AppStatus.SCANNING
            self._info(f"Scanning local: {', '.join(self.cfg.sync.sync_roots)}...")
            scanner = DirScanner(
                mount_point = self.cfg.mount.local_mount_path,
                sync_roots  = self.cfg.sync.sync_roots,
                node_name   = self.cfg.app.node_name,
            )
            local_snaps: dict[str, ScanSnapshot] = {}
            for snap in await scanner.scan_all():
                local_snaps[snap.sync_root] = snap
                self.repo.save_snapshot(snap)
                self._info(f"  /{snap.sync_root}: {snap.entry_count} folders, {human_size(snap.total_size)}")

            # 4. Scan agent(s)
            remote_snaps: dict[str, ScanSnapshot] = {}
            for i, agent in enumerate(self.agents):
                agent_cfg = self.cfg.agents[i]
                for root in self.cfg.sync.sync_roots:
                    self._info(f"Scanning agent '{agent_cfg.name}': /{root}...")
                    resp = await agent.list_dirs(root)
                    from ..scanner.models import ScanEntry
                    entries = [
                        ScanEntry(
                            relative_path=d.name,
                            entry_type="dir",
                            size=d.size_bytes,
                            mtime_utc=None,
                        )
                        for d in resp.dirs
                    ]
                    remote_snap = ScanSnapshot(
                        snapshot_id = "remote",
                        node_name   = agent_cfg.name,
                        sync_root   = root,
                        scanned_at  = utc_now_iso(),
                        entries     = entries,
                        entry_count = len(entries),
                        total_size  = sum(d.size_bytes for d in resp.dirs),
                    )
                    remote_snaps[root] = remote_snap
                    self._info(f"  /{root}: {remote_snap.entry_count} folders, {human_size(remote_snap.total_size)}")

            # 5. Compare
            self._info("Comparing local ↔ remote...")
            all_diffs = []
            for root in self.cfg.sync.sync_roots:
                diffs = compare(local_snaps[root], remote_snaps.get(root))
                all_diffs.extend(diffs)
                in_sync = sum(1 for d in diffs if d.diff_status.value == "in_sync")
                needs   = sum(1 for d in diffs if d.diff_status.value == "needs_sync")
                only_l  = sum(1 for d in diffs if d.diff_status.value == "local_only")
                only_r  = sum(1 for d in diffs if d.diff_status.value == "remote_only")
                self._info(f"  /{root}: {in_sync} in sync, {needs} different, {only_l} local only, {only_r} remote only")

            self._diffs = all_diffs

            # 6. Persist state
            state = self.repo.load_state(self.cfg.app.node_name, self.cfg.app.role.value)
            state.last_scan_id = next(iter(local_snaps.values())).snapshot_id if local_snaps else ""
            state.last_scan_at = utc_now_iso()
            state.diffs        = all_diffs
            self.repo.save_state(state)

            self._last_scan_at = state.last_scan_at
            self.status = AppStatus.READY
            self._info(f"✓ Scan complete — {len(all_diffs)} folders found")

        except Exception as e:
            self._error  = str(e)
            self._err(str(e))
            self.status = AppStatus.ERROR

        finally:
            if _did_mount:
                self._info("Unmounting...")
                try:
                    await self.nfs.unmount()
                except Exception as e:
                    self._err(f"Local unmount failed: {e}")
                for i, agent in enumerate(self.agents):
                    try:
                        await agent.unmount()
                    except Exception:
                        pass
                self._info("✓ NFS unmounted")

    async def run_sync(self, actions: list[ActionItem]):
        try:
            # Re-mount (scan already unmounted after completing)
            self._info(f"Mounting for sync: {self.nfs.host}:{self.nfs.share}...")
            info = await self.nfs.mount()
            assert_mount_ok(info)
            self._info("✓ Mounted")
            for i, agent in enumerate(self.agents):
                agent_cfg = self.cfg.agents[i]
                resp = await agent.mount()
                if not resp.ok:
                    raise RuntimeError(f"Agent mount failed: {resp.error}")
                self._info(f"✓ Agent '{agent_cfg.name}' mounted")

            # Update planned actions based on user selections
            action_map = {(a.sync_root, a.folder): a.action for a in actions}
            for diff in self._diffs:
                key = (diff.sync_root, diff.name)
                if key in action_map:
                    diff.planned_action = action_map[key]

            jobs = build_jobs(self._diffs)
            to_do = [j for j in jobs if j.action != SyncAction.SKIP]
            self._info(f"Starting sync — {len(to_do)} jobs...")

            if not self.cfg.agents:
                raise RuntimeError("No agents configured")
            agent_cfg = self.cfg.agents[0]
            if len(self.cfg.agents) > 1:
                log.warning("Multiple agents configured but sync only uses '%s'", agent_cfg.name)

            executor = SyncExecutor(
                local_mount  = self.cfg.mount.local_mount_path,
                remote_host  = agent_cfg.ssh_host or agent_cfg.host,
                remote_user  = agent_cfg.ssh_user,
                remote_mount = agent_cfg.remote_mount_path,
                ssh_key_file = agent_cfg.ssh_key_file,
            )

            for job in to_do:
                icon = "↑" if job.action == SyncAction.SYNC else "✕"
                self._info(f"{icon} {job.sync_root}/{job.folder}")
                async for line in executor.execute(job):
                    self._log.append(f"   {line}")

            self._info("Unmounting...")
            await self.nfs.unmount()
            for i, agent in enumerate(self.agents):
                await agent.unmount()
                self._info(f"Agent '{self.cfg.agents[i].name}' unmounted")

            state = self.repo.load_state(self.cfg.app.node_name, self.cfg.app.role.value)
            state.last_sync_at = utc_now_iso()
            state.jobs.extend(to_do)
            self.repo.save_state(state)

            self.status = AppStatus.DONE
            self._info("✓ Sync complete — NAS devices are idle")

        except Exception as e:
            self._error = str(e)
            self._err(str(e))
            self.status = AppStatus.ERROR

    async def do_unmount(self):
        try:
            await self.nfs.unmount()
        except Exception:
            pass
        for agent in self.agents:
            try:
                await agent.unmount()
            except Exception:
                pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _diff_to_dict(d: FolderDiff) -> dict:
    return {
        "sync_root":      d.sync_root,
        "name":           d.name,
        "diff_status":    d.diff_status.value,
        "local_size":     d.local_size,
        "remote_size":    d.remote_size,
        "local_size_hr":  d.local_size_hr,
        "remote_size_hr": d.remote_size_hr,
        "planned_action": d.planned_action.value,
    }
