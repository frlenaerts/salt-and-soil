"""Tests for the runtime's scan/sync mutual-exclusion claim logic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from salt_and_soil.config.models import (
    Config, AppConfig, ServerConfig, MountDefaults, SyncConfig, StateConfig,
    SourceConfig, AuthConfig,
)
from salt_and_soil.shared.enums import NodeRole, AppStatus
from salt_and_soil.roles.orchestrator import OrchestratorRuntime


def _runtime(tmp_path) -> OrchestratorRuntime:
    state_dir = tmp_path / "state"
    cfg = Config(
        app=AppConfig(role=NodeRole.ORCHESTRATOR, node_name="orch", data_dir=str(tmp_path)),
        server=ServerConfig(),
        mount_defaults=MountDefaults(),
        sync=SyncConfig(),
        state=StateConfig(state_file=str(state_dir / "state.json"), snapshot_dir=str(state_dir / "snapshots")),
        sources=[SourceConfig(alias="Projects"), SourceConfig(alias="Photos")],
        auth=AuthConfig(),
        agents=[],
    )
    return OrchestratorRuntime(cfg)


def test_scan_claim_blocks_second_scan(tmp_path):
    rt = _runtime(tmp_path)
    assert rt.try_begin_scan("alice") is True
    assert rt.status == AppStatus.MOUNTING
    assert rt.busy_op == "scan"
    assert rt.busy_user == "alice"
    # A second claim — scan or sync — is rejected while busy.
    assert rt.try_begin_scan("bob") is False
    assert rt.try_begin_sync("bob") is False
    # The holder is unchanged.
    assert rt.busy_user == "alice"


def test_sync_requires_ready_state(tmp_path):
    rt = _runtime(tmp_path)
    # Fresh runtime is IDLE → a sync cannot start (must scan first).
    assert rt.status == AppStatus.IDLE
    assert rt.try_begin_sync("alice") is False
    # Once a scan has produced a READY state, sync may claim.
    rt.status = AppStatus.READY
    assert rt.try_begin_sync("alice") is True
    assert rt.busy_op == "sync"
    assert rt.status == AppStatus.MOUNTING


def test_abort_begin_restores_prior_status(tmp_path):
    rt = _runtime(tmp_path)
    rt.status = AppStatus.READY
    assert rt.try_begin_sync("alice") is True
    rt.abort_begin()
    # Status rolls back to what it was before the claim; runtime is free again.
    assert rt.status == AppStatus.READY
    assert rt.busy_op is None
    assert rt.busy_user is None
    assert rt.try_begin_sync("bob") is True


def test_clear_busy_after_completion(tmp_path):
    rt = _runtime(tmp_path)
    assert rt.try_begin_scan("alice") is True
    rt._clear_busy()
    assert rt.busy_op is None
    assert rt.busy_user is None


def test_reset_keep_status_preserves_claim(tmp_path):
    rt = _runtime(tmp_path)
    assert rt.try_begin_scan("alice") is True
    rt._diffs = ["dummy"]  # type: ignore[list-item]
    rt.reset(set_idle=False)
    # Claim (busy status) survives; transient run data is cleared.
    assert rt.status == AppStatus.MOUNTING
    assert rt._diffs == []
    # Default reset returns to IDLE.
    rt.reset()
    assert rt.status == AppStatus.IDLE


def test_free_states_allow_scan(tmp_path):
    rt = _runtime(tmp_path)
    for st in (AppStatus.IDLE, AppStatus.READY, AppStatus.DONE, AppStatus.ERROR):
        rt.status = st
        rt._clear_busy()
        assert rt.try_begin_scan() is True, f"scan should be allowed from {st}"
        rt.abort_begin()
    for st in (AppStatus.MOUNTING, AppStatus.SCANNING, AppStatus.SYNCING):
        rt.status = st
        assert rt.try_begin_scan() is False, f"scan should be blocked from {st}"
