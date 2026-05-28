"""
OrchestratorRuntime manages the full sync lifecycle:
  mount → scan (local + agent) → compare → ready → sync → unmount
"""
from __future__ import annotations

import logging
import posixpath
from pathlib import Path
from typing import Any

from ..config.models import Config, SourceConfig, AgentConfig
from ..mounts.registry import MountRegistry, _slug
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
from ..sync.executor import SyncExecutor, ResolvedSource
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
        self._mounts_info: list[dict] = []
        self._error: str = ""
        self._last_scan_at: str | None = None
        self._cancel_requested: bool = False
        self._current_executor: SyncExecutor | None = None

        # Per (host, share) mount registry for the local NAS
        self.registry = MountRegistry(cfg.mount_defaults, side="local")

        # Index sources by alias and agents by name (cfg.sources is already
        # sorted by (sort, alias) by the loader)
        self.sources_by_alias: dict[str, SourceConfig] = {s.alias: s for s in cfg.sources}
        self.agents_by_name: dict[str, AgentConfig]    = {a.name: a for a in cfg.agents}
        self.agent_clients: dict[str, AgentAPIClient]  = {
            a.name: AgentAPIClient(base_url=f"http://{a.host}:{a.port}", api_key=a.api_key)
            for a in cfg.agents
        }

        # State repo
        self.repo = StateRepository(
            state_file   = cfg.state.state_file,
            snapshot_dir = cfg.state.snapshot_dir,
        )

        # Schedule
        schedule_file = str(Path(cfg.state.state_file).parent / "schedule.json")
        self._schedule_store = ScheduleStore(schedule_file)
        self._schedule       = self._schedule_store.load()
        self._schedule_loop  = ScheduleLoop(self)

    # ── Source-path resolution ────────────────────────────────────────────────

    def _local_scan_path(self, src: SourceConfig) -> str:
        mp = self.registry.get_or_create(src.local_host, src.local_share).mount_point
        return posixpath.join(mp, src.local_path) if src.local_path else mp

    def _remote_scan_path(self, src: SourceConfig) -> str:
        """Where the SAME source is reachable on the agent side, as seen via
        ssh+rsync. The agent uses the same slug convention via its own registry,
        so we can derive its mount point from MountDefaults.mount_root_remote."""
        # Agent's local NAS host is in its own config — orchestrator never sees
        # it. We assume the agent mounts its (remote_share) at the same slug
        # under mount_root_remote. The slug here intentionally uses the share
        # only, NOT a host, because the orchestrator doesn't know the agent's
        # NAS host. Picks up the agent's share via the registry-slug convention:
        # the agent's MountRegistry uses (its_host, its_share); we match on
        # share alone to construct a path that matches the agent's slug only
        # if its host slug happens to align — instead, the orchestrator gets
        # the agent-side mount_point directly from /status responses. This
        # function is therefore a best-effort fallback used to seed
        # source_map.remote_full_path; the agent /status endpoint is the
        # authoritative source.
        # NOTE: prefer to overwrite via _refresh_remote_paths() after mount.
        slug = _slug(src.remote_share)
        mp   = posixpath.join(self.cfg.mount_defaults.mount_root_remote, f"agent_{slug}")
        return posixpath.join(mp, src.remote_path) if src.remote_path else mp

    def _build_source_map(self, remote_mount_points: dict[str, str]) -> dict[str, ResolvedSource]:
        """Build the alias→ResolvedSource map used by SyncExecutor.
        `remote_mount_points` maps alias → agent-reported mount_point (from /status)."""
        out: dict[str, ResolvedSource] = {}
        for src in self.cfg.sources:
            local_full  = self._local_scan_path(src)
            remote_mp   = remote_mount_points.get(src.alias)
            if remote_mp:
                remote_full = posixpath.join(remote_mp, src.remote_path) if src.remote_path else remote_mp
            else:
                remote_full = self._remote_scan_path(src)
            out[src.alias] = ResolvedSource(local_full_path=local_full, remote_full_path=remote_full)
        return out

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
        self._mounts_info = []
        self._error      = ""
        self._cancel_requested = False

    def clear_log(self):
        """Clear log history without touching status or diffs. Keeps log_total
        so SSE clients don't see a bogus positive delta on the next line."""
        self._log = []

    # ── UI snapshot ───────────────────────────────────────────────────────────

    def snapshot_for_ui(self) -> dict[str, Any]:
        return {
            "status":       self.status.value,
            "log":          list(self._log),
            "log_total":    self._log_total,
            "diffs":        [_diff_to_dict(d) for d in self._diffs],
            "mounts":       list(self._mounts_info),
            "error":        self._error,
            "last_scan_at": self._last_scan_at,
            "schedule":     self._schedule.to_dict(),
            "cancelled":    self._cancel_requested,
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
        """Scheduled trigger: full scan, then auto-sync every folder whose
        diff_status is needs_sync. Every other folder is explicitly marked
        SKIP — the comparer sets planned_action=SYNC for local_only, and
        run_sync only overrides actions it receives, so skipping needs to
        be stated explicitly for those folders."""
        self._error = ""
        self._info(f"[{self._node}] ⏰ Scheduled run starting")
        await self.run_scan()
        if self.status != AppStatus.READY:
            return
        actions = [
            ActionItem(
                source_alias = d.source_alias,
                folder       = d.name,
                action       = SyncAction.SYNC if d.diff_status == DiffStatus.NEEDS_SYNC else SyncAction.SKIP,
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

    async def _do_mount_all(self) -> dict[str, str]:
        """Mount all unique (local_host, local_share) pairs locally, and call
        the agent mount endpoint for each source. Returns alias → remote
        mount_point (as reported by the agent /status, when available)."""
        # 1. Local: dedup by (host, share)
        unique_pairs = sorted({(s.local_host, s.local_share) for s in self.cfg.sources})
        self._mounts_info = []
        for host, share in unique_pairs:
            self._info(f"[{self._node}] Mounting {share}...")
            nfs  = self.registry.get_or_create(host, share)
            info = await nfs.mount()
            self._mounts_info.append({
                "side":        "local",
                "host":        host,
                "share":       share,
                "mount_point": nfs.mount_point,
                "status":      info.status.value,
                "writable":    info.writable,
                "total":       human_size(info.total_bytes),
                "free":        human_size(info.free_bytes),
            })
            assert_mount_ok(info)
            self._info(f"[{self._node}] Mounted {share} — {human_size(info.total_bytes)} total, {human_size(info.free_bytes)} free")
            if is_path_empty(nfs.mount_point):
                raise MountCheckError(f"Mount path {nfs.mount_point} is empty — NFS share may not be configured correctly")

        # 2. Remote: call agent.mount() for every alias (the agent's own
        # MountRegistry dedups internally), but log only one line per unique
        # remote_share so the orchestrator's log mirrors the local section.
        remote_mount_points: dict[str, str] = {}
        for agent_name, agent in self.agent_clients.items():
            logged_shares: set[str] = set()
            for src in (s for s in self.cfg.sources if s.agent == agent_name):
                first_for_share = src.remote_share not in logged_shares
                if first_for_share:
                    self._info(f"[{agent_name}] Mounting {src.remote_share}...")
                resp = await agent.mount(src.alias)
                if not resp.ok:
                    raise RuntimeError(f"[{agent_name}] Mount {src.remote_share} failed: {resp.error}")
                if first_for_share:
                    logged_shares.add(src.remote_share)
                    size_info = (
                        f" — {human_size(resp.total_bytes)} total, {human_size(resp.free_bytes)} free"
                        if resp.total_bytes else ""
                    )
                    self._info(f"[{agent_name}] Mounted {src.remote_share}{size_info}")

            # Pull mount points from /status so we know where the agent actually mounted
            try:
                st = await agent.status()
                for m in st.mounts:
                    if m.get("alias") in self.sources_by_alias:
                        remote_mount_points[m["alias"]] = m["mount_point"]
            except Exception as e:
                log.warning("Could not fetch /status from %s: %s", agent_name, e)

        return remote_mount_points

    async def _do_unmount_all(self) -> None:
        # Local: one log line per share
        for nfs in self.registry.all():
            try:
                await nfs.unmount()
                self._info(f"[{self._node}] Unmounted {nfs.share}")
            except Exception as e:
                self._err(f"[{self._node}] Unmount {nfs.share} failed: {e}")
        # Remote: unmount every alias (agent dedups internally), log once per share
        for agent_name, agent in self.agent_clients.items():
            logged_shares: set[str] = set()
            for src in (s for s in self.cfg.sources if s.agent == agent_name):
                first_for_share = src.remote_share not in logged_shares
                try:
                    await agent.unmount(src.alias)
                    if first_for_share:
                        logged_shares.add(src.remote_share)
                        self._info(f"[{agent_name}] Unmounted {src.remote_share}")
                except Exception as e:
                    if first_for_share:
                        logged_shares.add(src.remote_share)
                        self._err(f"[{agent_name}] Unmount {src.remote_share} failed: {e}")

    async def _do_scan_and_compare(self) -> None:
        """Scan both sides, compare, persist diffs. Assumes mounts are active."""
        self.status = AppStatus.SCANNING
        aliases = [s.alias for s in self.cfg.sources]
        self._info(f"[{self._node}] Scanning: {', '.join(aliases)}...")
        scanner = DirScanner(
            node_name = self.cfg.app.node_name,
            excludes  = self.cfg.sync.excludes,
        )
        local_snaps: dict[str, ScanSnapshot] = {}
        for src in self.cfg.sources:
            scan_path = self._local_scan_path(src)
            snap      = await scanner.scan_source(scan_path, src.alias)
            local_snaps[src.alias] = snap
            self.repo.save_snapshot(snap)
            self._info(f"[{self._node}] {src.alias}: {snap.entry_count} folders, {human_size(snap.total_size)}")

        remote_snaps: dict[str, ScanSnapshot] = {}
        for src in self.cfg.sources:
            agent_name = src.agent
            agent      = self.agent_clients.get(agent_name)
            if not agent:
                self._err(f"[{src.alias}] No client for agent '{agent_name}' — skipping remote scan")
                continue
            self._info(f"[{agent_name}] Scanning '{src.alias}'...")
            resp = await agent.list_dirs(src.alias)
            from ..scanner.models import ScanEntry
            entries = [
                ScanEntry(
                    relative_path = d.name,
                    entry_type    = "dir",
                    size          = d.size_bytes,
                    mtime_utc     = None,
                )
                for d in resp.dirs
            ]
            remote_snap = ScanSnapshot(
                snapshot_id  = "remote",
                node_name    = agent_name,
                source_alias = src.alias,
                scanned_at   = utc_now_iso(),
                entries      = entries,
                entry_count  = len(entries),
                total_size   = sum(d.size_bytes for d in resp.dirs),
            )
            remote_snaps[src.alias] = remote_snap
            self._info(f"[{agent_name}] {src.alias}: {remote_snap.entry_count} folders, {human_size(remote_snap.total_size)}")

        self._info(f"[{self._node}] Comparing with agent...")
        all_diffs: list[FolderDiff] = []
        for src in self.cfg.sources:
            diffs   = compare(local_snaps[src.alias], remote_snaps.get(src.alias))
            all_diffs.extend(diffs)
            in_sync = sum(1 for d in diffs if d.diff_status.value == "in_sync")
            needs   = sum(1 for d in diffs if d.diff_status.value == "needs_sync")
            only_l  = sum(1 for d in diffs if d.diff_status.value == "local_only")
            only_r  = sum(1 for d in diffs if d.diff_status.value == "remote_only")
            self._info(f"[{self._node}] {src.alias}: {in_sync} in sync, {needs} different, {only_l} local only, {only_r} remote only")

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
        self._cancel_requested = False
        _did_mount = False
        try:
            await self._do_mount_all()
            _did_mount = True
            await self._do_scan_and_compare()
            self.status = AppStatus.READY
        except Exception as e:
            self._error = str(e)
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
            remote_mps = await self._do_mount_all()
            _did_mount = True

            action_map = {(a.source_alias, a.folder): a.action for a in actions}
            for diff in self._diffs:
                k = (diff.source_alias, diff.name)
                if k in action_map:
                    diff.planned_action = action_map[k]

            jobs  = build_jobs(self._diffs)
            to_do = [j for j in jobs if j.action != SyncAction.SKIP]
            self.status = AppStatus.SYNCING
            self._info(f"[{self._node}] Starting sync — {len(to_do)} jobs...")

            # Group jobs by agent (each source belongs to exactly one agent);
            # build one executor per agent so each job rsyncs to the correct host.
            source_map = self._build_source_map(remote_mps)
            executors_by_agent: dict[str, SyncExecutor] = {}
            for agent_name, agent_cfg in self.agents_by_name.items():
                executors_by_agent[agent_name] = SyncExecutor(
                    source_map   = source_map,
                    remote_host  = agent_cfg.ssh_host or agent_cfg.host,
                    remote_user  = agent_cfg.ssh_user,
                    ssh_key_file = agent_cfg.ssh_key_file,
                    remote_name  = agent_cfg.name,
                    exclude_file = self.cfg.sync.exclude_file,
                )

            completed = []
            for job in to_do:
                if self._cancel_requested:
                    break
                src = self.sources_by_alias.get(job.source_alias)
                if not src:
                    self._err(f"Skipping job for unknown source '{job.source_alias}'")
                    continue
                executor = executors_by_agent.get(src.agent)
                if not executor:
                    self._err(f"Skipping job '{job.source_alias}/{job.folder}': no executor for agent '{src.agent}'")
                    continue
                self._current_executor = executor
                icon = {
                    SyncAction.SYNC:          "↑",
                    SyncAction.PULL:          "↓",
                    SyncAction.DELETE_REMOTE: "✕",
                }.get(job.action, "?")
                self._info(f"[{self._node}] {icon} {job.source_alias}/{job.folder}")
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
        for nfs in self.registry.all():
            try:
                await nfs.unmount()
            except Exception:
                pass
        for agent in self.agent_clients.values():
            try:
                await agent.unmount()
            except Exception:
                pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _diff_to_dict(d: FolderDiff) -> dict:
    return {
        "source_alias":   d.source_alias,
        "name":           d.name,
        "diff_status":    d.diff_status.value,
        "local_size":     d.local_size,
        "remote_size":    d.remote_size,
        "local_size_hr":  d.local_size_hr,
        "remote_size_hr": d.remote_size_hr,
        "planned_action": d.planned_action.value,
    }
