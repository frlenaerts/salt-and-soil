"""
OrchestratorRuntime manages the full sync lifecycle:
  mount → scan (local + agent) → compare → ready → sync → unmount
"""
from __future__ import annotations

import logging
from typing import Any

from pathlib import Path

from ..config.models import Config
from ..mounts.nfs import NFSMount
from ..mounts.checks import assert_mount_ok, MountCheckError, is_path_empty
from ..scanner.scanner import DirScanner
from ..scanner.models import ScanSnapshot
from ..schedule.models import Schedule
from ..schedule.store import ScheduleStore
from ..schedule.loop import ScheduleLoop
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

_LOG_CAP = 500


class OrchestratorRuntime:
    def __init__(self, cfg: Config):
        self.cfg    = cfg
        self.status = AppStatus.IDLE
        self._log:  list[str] = []
        self._log_total: int = 0
        self._diffs: list[FolderDiff] = []
        self._mount_info: dict | None = None
        self._error: str = ""
        self._last_scan_at: str | None = None
        self._cancel_requested: bool = False
        self._current_executor: SyncExecutor | None = None

        # NFS mount for the local NAS
        self.nfs = NFSMount(
            host        = cfg.mount.remote_host,
            share       = cfg.mount.remote_share,
            mount_point = cfg.mount.local_mount_path,
            nfs_version = cfg.mount.nfs_version,
            nfs_options = cfg.mount.nfs_options,
            retry_delay = cfg.mount.mount_retry_delay,
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

        # Schedule: in-memory copy backed by schedule.json next to state.json
        schedule_file = str(Path(cfg.state.state_file).parent / "schedule.json")
        self._schedule_store = ScheduleStore(schedule_file)
        self._schedule       = self._schedule_store.load()
        self._schedule_loop  = ScheduleLoop(self)

    # ── Logging ───────────────────────────────────────────────────────────────

    @property
    def _node(self) -> str:
        return self.cfg.app.node_name

    @staticmethod
    def _ts() -> str:
        from datetime import datetime
        return datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    def _append_log(self, line: str):
        self._log.append(line)
        self._log_total += 1
        if len(self._log) > _LOG_CAP:
            self._log = self._log[-_LOG_CAP:]

    def _info(self, msg: str):
        log.info(msg)
        self._append_log(f"{self._ts()} - {msg}")

    def _err(self, msg: str):
        log.error(msg)
        self._append_log(f"{self._ts()} - ⚠ {msg}")

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset(self):
        self.status      = AppStatus.IDLE
        self._log        = []
        self._log_total  = 0
        self._diffs      = []
        self._mount_info = None
        self._error      = ""

    # ── UI snapshot ───────────────────────────────────────────────────────────

    def snapshot_for_ui(self) -> dict[str, Any]:
        return {
            "status":       self.status.value,
            "log":          list(self._log),
            "log_total":    self._log_total,
            "diffs":        [_diff_to_dict(d) for d in self._diffs],
            "mount":        self._mount_info,
            "error":        self._error,
            "last_scan_at": self._last_scan_at,
            "schedule":     self._schedule.to_dict(),
        }

    # ── Schedule ──────────────────────────────────────────────────────────────

    def get_schedule(self) -> Schedule:
        return self._schedule

    def save_schedule(self, s: Schedule) -> None:
        self._schedule = s
        self._schedule_store.save(s)

    async def start_schedule_loop(self) -> None:
        self._schedule_loop.start()

    async def stop_schedule_loop(self) -> None:
        await self._schedule_loop.stop()

    async def run_scheduled_cycle(self) -> None:
        """
        Scheduled trigger: full scan, then auto-sync every folder whose
        diff_status is needs_sync. Every other folder is explicitly marked
        SKIP — the comparer sets planned_action=SYNC for local_only, and
        run_sync only overrides actions it receives, so skipping needs to
        be stated explicitly for those folders.
        """
        self._error = ""
        self._info(f"[{self._node}] ⏰ Scheduled run starting")
        await self.run_scan()
        if self.status != AppStatus.READY:
            return
        actions = [
            ActionItem(
                sync_root = d.sync_root,
                folder    = d.name,
                action    = SyncAction.SYNC if d.diff_status == DiffStatus.NEEDS_SYNC else SyncAction.SKIP,
            )
            for d in self._diffs
        ]
        to_sync = sum(1 for a in actions if a.action == SyncAction.SYNC)
        if not to_sync:
            self._info(f"[{self._node}] ⏰ Scheduled run — nothing to sync")
            return
        self._info(f"[{self._node}] ⏰ Scheduled run — {to_sync} folder(s) to sync")
        await self.run_sync(actions)

    # ── Main flow ─────────────────────────────────────────────────────────────

    async def _do_mount_all(self) -> None:
        """Mount orchestrator NAS + all agent NAS devices. Raises on failure."""
        self._info(f"[{self._node}] Mounting {self.nfs.host}:{self.nfs.share}...")
        info = await self.nfs.mount()
        self._mount_info = {
            "source":      info.source,
            "local_path":  info.local_path,
            "status":      info.status.value,
            "writable":    info.writable,
            "total":       human_size(info.total_bytes),
            "free":        human_size(info.free_bytes),
        }
        assert_mount_ok(info)
        self._info(f"[{self._node}] Mounted — {human_size(info.total_bytes)} total, {human_size(info.free_bytes)} free")

        if is_path_empty(self.cfg.mount.local_mount_path):
            raise MountCheckError("Mount path is empty — NFS share may not be configured correctly")

        for i, agent in enumerate(self.agents):
            agent_cfg = self.cfg.agents[i]
            self._info(f"[{agent_cfg.name}] Mounting {agent_cfg.host}...")
            resp = await agent.mount()
            if not resp.ok:
                raise RuntimeError(f"[{agent_cfg.name}] Mount failed: {resp.error}")
            size_info = f" — {human_size(resp.total_bytes)} total, {human_size(resp.free_bytes)} free" if resp.total_bytes else ""
            self._info(f"[{agent_cfg.name}] Mounted{size_info}")

    async def _do_unmount_all(self) -> None:
        try:
            await self.nfs.unmount()
            self._info(f"[{self._node}] Unmounted")
        except Exception as e:
            self._err(f"[{self._node}] Unmount failed: {e}")
        for i, agent in enumerate(self.agents):
            try:
                await agent.unmount()
                self._info(f"[{self.cfg.agents[i].name}] Unmounted")
            except Exception as e:
                self._err(f"[{self.cfg.agents[i].name}] Unmount failed: {e}")

    async def _do_scan_and_compare(self) -> None:
        """Scan both sides, compare, persist diffs. Assumes mounts are active."""
        self.status = AppStatus.SCANNING
        self._info(f"[{self._node}] Scanning: {', '.join(self.cfg.sync.sync_roots)}...")
        scanner = DirScanner(
            mount_point = self.cfg.mount.local_mount_path,
            sync_roots  = self.cfg.sync.sync_roots,
            node_name   = self.cfg.app.node_name,
            excludes    = self.cfg.sync.excludes,
        )
        local_snaps: dict[str, ScanSnapshot] = {}
        for snap in await scanner.scan_all():
            local_snaps[snap.sync_root] = snap
            self.repo.save_snapshot(snap)
            self._info(f"[{self._node}] /{snap.sync_root}: {snap.entry_count} folders, {human_size(snap.total_size)}")

        remote_snaps: dict[str, ScanSnapshot] = {}
        for i, agent in enumerate(self.agents):
            agent_cfg = self.cfg.agents[i]
            for root in self.cfg.sync.sync_roots:
                self._info(f"[{agent_cfg.name}] Scanning /{root}...")
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
                self._info(f"[{agent_cfg.name}] /{root}: {remote_snap.entry_count} folders, {human_size(remote_snap.total_size)}")

        self._info(f"[{self._node}] Comparing with agent...")
        all_diffs = []
        for root in self.cfg.sync.sync_roots:
            diffs = compare(local_snaps[root], remote_snaps.get(root))
            all_diffs.extend(diffs)
            in_sync = sum(1 for d in diffs if d.diff_status.value == "in_sync")
            needs   = sum(1 for d in diffs if d.diff_status.value == "needs_sync")
            only_l  = sum(1 for d in diffs if d.diff_status.value == "local_only")
            only_r  = sum(1 for d in diffs if d.diff_status.value == "remote_only")
            self._info(f"[{self._node}] /{root}: {in_sync} in sync, {needs} different, {only_l} local only, {only_r} remote only")

        self._diffs = all_diffs

        state = self.repo.load_state(self.cfg.app.node_name, self.cfg.app.role.value)
        state.last_scan_id = next(iter(local_snaps.values())).snapshot_id if local_snaps else ""
        state.last_scan_at = utc_now_iso()
        state.diffs        = all_diffs
        self.repo.save_state(state)

        self._last_scan_at = state.last_scan_at
        self._info(f"[{self._node}] Scan complete — {len(all_diffs)} folders found")

    async def run_scan(self):
        self.status = AppStatus.MOUNTING
        _did_mount  = False
        try:
            await self._do_mount_all()
            _did_mount = True
            await self._do_scan_and_compare()
            self.status = AppStatus.READY
        except Exception as e:
            self._error  = str(e)
            self._err(str(e))
            self.status = AppStatus.ERROR
        finally:
            if _did_mount:
                await self._do_unmount_all()

    async def run_sync(self, actions: list[ActionItem], rescan_after: bool = True):
        _did_mount = False
        self._cancel_requested = False
        try:
            self.status = AppStatus.MOUNTING
            await self._do_mount_all()
            _did_mount = True

            action_map = {(a.sync_root, a.folder): a.action for a in actions}
            for diff in self._diffs:
                k = (diff.sync_root, diff.name)
                if k in action_map:
                    diff.planned_action = action_map[k]

            jobs = build_jobs(self._diffs)
            to_do = [j for j in jobs if j.action != SyncAction.SKIP]
            self.status = AppStatus.SYNCING
            self._info(f"[{self._node}] Starting sync — {len(to_do)} jobs...")

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
                remote_name  = agent_cfg.name,
                exclude_file = self.cfg.sync.exclude_file,
            )
            self._current_executor = executor

            completed = []
            for job in to_do:
                if self._cancel_requested:
                    break
                icon = {
                    SyncAction.SYNC:          "↑",
                    SyncAction.PULL:          "↓",
                    SyncAction.DELETE_REMOTE: "✕",
                }.get(job.action, "?")
                self._info(f"[{self._node}] {icon} {job.sync_root}/{job.folder}")
                async for line in executor.execute(job):
                    self._append_log(f"{self._ts()} - [{self._node}]    {line}")
                completed.append(job)

            self._current_executor = None

            state = self.repo.load_state(self.cfg.app.node_name, self.cfg.app.role.value)
            state.last_sync_at = utc_now_iso()
            state.jobs.extend(completed)
            self.repo.save_state(state)

            if self._cancel_requested:
                self._info(f"[{self._node}] Sync cancelled — rescanning to refresh folder status")
            else:
                self._info(f"[{self._node}] Sync complete")

            if rescan_after:
                await self._do_scan_and_compare()
                self.status = AppStatus.READY
            else:
                self.status = AppStatus.DONE

        except Exception as e:
            self._error = str(e)
            self._err(str(e))
            self.status = AppStatus.ERROR
        finally:
            self._current_executor = None
            if _did_mount:
                await self._do_unmount_all()

    async def request_cancel(self) -> bool:
        """Request cancellation of an in-progress sync. Terminates the current
        rsync subprocess and breaks out of the job loop; run_sync then rescans
        to refresh folder state. No-op if not currently syncing."""
        if self.status != AppStatus.SYNCING or self._cancel_requested:
            return False
        self._cancel_requested = True
        self._info(f"[{self._node}] ✕ Cancel requested — stopping current transfer")
        ex = self._current_executor
        if ex is not None:
            ex.cancel()
        return True

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
