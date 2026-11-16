"""
test_state_machine.py — the server decides what may happen when.

  * a run in flight cannot be disturbed (/api/reset) or doubled (/api/start,
    /api/execute)
  * the action a client asks for must make sense for the folder's status —
    the dropdown in the UI is a convenience, not the rule
"""
import pytest

from salt_and_soil.shared.enums import AppStatus, DiffStatus, SyncAction
from salt_and_soil.state.models import FolderDiff
from salt_and_soil.sync.planner import ALLOWED_ACTIONS, is_action_allowed
from salt_and_soil.transport.dtos import ActionItem

S, A = DiffStatus, SyncAction
BUSY = [AppStatus.MOUNTING, AppStatus.SCANNING, AppStatus.SYNCING]
FREE = [AppStatus.IDLE, AppStatus.READY, AppStatus.DONE, AppStatus.ERROR]


# ── allowed actions ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("status, action, allowed", [
    (S.IN_SYNC,     A.SKIP, True),  (S.IN_SYNC,     A.DELETE_REMOTE, True),
    (S.IN_SYNC,     A.SYNC, False), (S.IN_SYNC,     A.PULL,          False),
    (S.NEEDS_SYNC,  A.SYNC, True),  (S.NEEDS_SYNC,  A.SKIP,          True),
    (S.NEEDS_SYNC,  A.DELETE_REMOTE, True), (S.NEEDS_SYNC, A.PULL,   False),
    (S.LOCAL_ONLY,  A.SYNC, True),  (S.LOCAL_ONLY,  A.SKIP,          True),
    (S.LOCAL_ONLY,  A.PULL, False), (S.LOCAL_ONLY,  A.DELETE_REMOTE, False),
    (S.REMOTE_ONLY, A.SKIP, True),  (S.REMOTE_ONLY, A.PULL,          True),
    (S.REMOTE_ONLY, A.DELETE_REMOTE, True), (S.REMOTE_ONLY, A.SYNC,  False),
    (S.UNKNOWN,     A.SKIP, True),  (S.UNKNOWN,     A.SYNC,          False),
    (S.UNKNOWN,     A.PULL, False), (S.UNKNOWN,     A.DELETE_REMOTE, False),
])
def test_action_matrix(status, action, allowed):
    assert is_action_allowed(status, action) is allowed


def test_matrix_matches_the_choices_offered_by_the_ui():
    """ACTION_OPTS in index.html and ALLOWED_ACTIONS must not drift apart."""
    import re
    from pathlib import Path
    html = (Path(__file__).parent.parent / "src/salt_and_soil/templates/index.html").read_text()
    block = re.search(r"const ACTION_OPTS = \{(.*?)\};", html, re.S).group(1)
    ui = {
        status: set(re.findall(r"\['(\w+)',", opts))
        for status, opts in re.findall(r"(\w+):\s*\[(.*?)\],?\n", block)
    }
    server = {s.value: {a.value for a in acts} for s, acts in ALLOWED_ACTIONS.items()}
    assert ui == server


# ── helpers ───────────────────────────────────────────────────────────────────

def _diffs():
    return [
        FolderDiff("videos", "Local Only",  S.LOCAL_ONLY,  planned_action=A.SYNC),
        FolderDiff("videos", "Remote Only", S.REMOTE_ONLY),
        FolderDiff("videos", "Changed",     S.NEEDS_SYNC,  planned_action=A.SYNC),
        FolderDiff("videos", "Same",        S.IN_SYNC),
    ]


def _action(folder, action, alias="videos"):
    return {"source_alias": alias, "folder": folder, "action": action}


@pytest.fixture
def app(orch_user):
    """Signed-in client on a runtime whose long-running parts are recorded
    instead of executed."""
    rt = orch_user.rt
    calls = {"scan": 0, "sync": [], "unmount": 0}

    async def run_scan():
        calls["scan"] += 1

    async def run_sync(actions, rescan_after=True):
        calls["sync"].append(actions)

    async def do_unmount():
        calls["unmount"] += 1
        calls["status_during_unmount"] = rt.status

    rt.run_scan, rt.run_sync, rt.do_unmount = run_scan, run_sync, do_unmount
    orch_user.calls = calls
    orch_user.c = orch_user.login()
    return orch_user


# ── /api/reset ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", BUSY)
def test_reset_is_refused_while_a_run_is_in_flight(app, status):
    app.rt.status = status
    r = app.c.post("/api/reset")
    assert r.status_code == 400
    assert app.calls["unmount"] == 0, "must not unmount underneath a running scan or sync"
    assert app.rt.status == status


@pytest.mark.parametrize("status", FREE)
def test_reset_works_when_nothing_is_running(app, status):
    app.rt.status = status
    app.rt._diffs = _diffs()
    r = app.c.post("/api/reset")
    assert r.status_code == 200
    assert app.calls["unmount"] == 1
    assert app.calls["status_during_unmount"] in BUSY, "runtime must be held while unmounting"
    assert app.rt.status == AppStatus.IDLE
    assert app.rt._diffs == []


# ── /api/start ────────────────────────────────────────────────────────────────

def test_start_claims_the_runtime_before_the_scan_task_runs(app):
    r = app.c.post("/api/start")
    assert r.status_code == 200
    assert app.calls["scan"] == 1
    # The stand-in scan never changes the status, so what we see is what the
    # handler set before handing over — a second request must find it busy.
    assert app.rt.status == AppStatus.MOUNTING
    assert app.c.post("/api/start").status_code == 400
    assert app.calls["scan"] == 1


@pytest.mark.parametrize("status", BUSY)
def test_start_is_refused_while_busy(app, status):
    app.rt.status = status
    assert app.c.post("/api/start").status_code == 400
    assert app.calls["scan"] == 0


# ── /api/execute ──────────────────────────────────────────────────────────────

def test_execute_runs_valid_actions_once(app):
    app.rt.status, app.rt._diffs = AppStatus.READY, _diffs()
    body = {"actions": [_action("Local Only", "sync"), _action("Remote Only", "delete_remote"),
                        _action("Changed", "skip"), _action("Same", "skip")]}
    r = app.c.post("/api/execute", json=body)
    assert r.status_code == 200
    assert app.rt.status == AppStatus.SYNCING
    assert len(app.calls["sync"]) == 1
    assert [(a.folder, a.action) for a in app.calls["sync"][0]] == [
        ("Local Only", A.SYNC), ("Remote Only", A.DELETE_REMOTE), ("Changed", A.SKIP), ("Same", A.SKIP)]

    # second request while the first is syncing
    assert app.c.post("/api/execute", json=body).status_code == 400
    assert len(app.calls["sync"]) == 1


@pytest.mark.parametrize("folder, action", [
    ("Local Only",  "pull"),            # nothing to pull
    ("Local Only",  "delete_remote"),   # nothing to delete
    ("Remote Only", "sync"),            # nothing to push
    ("Same",        "sync"),
    ("Same",        "pull"),
    ("Changed",     "pull"),
])
def test_execute_rejects_actions_that_do_not_fit_the_folder_status(app, folder, action):
    app.rt.status, app.rt._diffs = AppStatus.READY, _diffs()
    r = app.c.post("/api/execute", json={"actions": [_action("Changed", "sync"), _action(folder, action)]})
    assert r.status_code == 400
    assert f"Action '{action}' is not allowed for videos/{folder}" in r.json()["detail"]
    assert app.calls["sync"] == [], "nothing may run when any action is invalid"
    assert app.rt.status == AppStatus.READY


def test_execute_ignores_rows_that_no_longer_exist(app):
    app.rt.status, app.rt._diffs = AppStatus.READY, _diffs()
    r = app.c.post("/api/execute", json={"actions": [_action("Gone", "delete_remote"), _action("x", "pull", alias="nope")]})
    assert r.status_code == 200


@pytest.mark.parametrize("body", [
    {"actions": [{"source_alias": "videos", "folder": "Same"}]},                       # no action
    {"actions": [_action("Same", "format_disk")]},                                     # unknown action
    {"actions": [{"source_alias": ["videos"], "folder": "Same", "action": "skip"}]},   # wrong type
    {"actions": "sync"},
    {"actions": [None]},
])
def test_execute_rejects_malformed_payloads(app, body):
    app.rt.status, app.rt._diffs = AppStatus.READY, _diffs()
    r = app.c.post("/api/execute", json=body)
    assert r.status_code == 400
    assert app.calls["sync"] == []
    assert app.rt.status == AppStatus.READY


@pytest.mark.parametrize("status", [s for s in AppStatus if s != AppStatus.READY])
def test_execute_needs_a_finished_scan(app, status):
    app.rt.status = status
    r = app.c.post("/api/execute", json={"actions": []})
    assert r.status_code == 400
    assert app.calls["sync"] == []


# ── run_sync backstop ─────────────────────────────────────────────────────────

async def test_run_sync_skips_a_disallowed_action_even_if_it_gets_that_far(orch_user, monkeypatch):
    """run_sync is also called by the scheduler, without the API in front."""
    from salt_and_soil.sync import executor as executor_module
    rt = orch_user.rt
    rt._diffs = [
        FolderDiff("videos", "Local Only", S.LOCAL_ONLY, planned_action=A.SKIP),
        FolderDiff("videos", "Changed",    S.NEEDS_SYNC, planned_action=A.SKIP),
    ]
    ran = []

    async def no_mounts():
        return {}

    async def nothing():
        pass

    async def fake_execute(self, job):
        ran.append((job.folder, job.action))
        return
        yield   # pragma: no cover — makes this an async generator

    monkeypatch.setattr(rt, "_do_mount_all", no_mounts)
    monkeypatch.setattr(rt, "_do_unmount_all", nothing)
    monkeypatch.setattr(executor_module.SyncExecutor, "execute", fake_execute)

    await rt.run_sync([
        ActionItem("videos", "Local Only", A.DELETE_REMOTE),   # not allowed
        ActionItem("videos", "Changed",    A.SYNC),            # fine
    ], rescan_after=False)

    assert ran == [("Changed", A.SYNC)]
    assert any("Skipping videos/Local Only" in line for line in rt._log)
