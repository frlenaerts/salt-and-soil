"""
test_login_throttle.py — failed logins are throttled per client, with a
global ceiling, and a login attempt costs the same whether or not the user
name exists.

Before: one global counter. Five wrong attempts from any device locked the
operator out for 15 minutes — repeatable forever.
"""
import pytest

import salt_and_soil.auth.throttle as throttle_mod
import salt_and_soil.transport.api_server as api_server
from salt_and_soil.auth.password import MAX_PASSWORD_LENGTH
from salt_and_soil.auth.throttle import LoginThrottle, client_key

from conftest import TEST_PASSWORD, TEST_USER

GOOD = {"username": TEST_USER, "password": TEST_PASSWORD}
BAD  = {"username": TEST_USER, "password": "wrong-password"}


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(throttle_mod.time, "monotonic", lambda: now[0])
    return now


# ── client_key ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("host, key", [
    ("192.168.1.20",               "192.168.1.20"),
    ("::ffff:192.168.1.20",        "192.168.1.20"),
    ("fd7a:115c:a1e0::1",          "fd7a:115c:a1e0::/64"),
    ("fd7a:115c:a1e0:0:ffff::9",   "fd7a:115c:a1e0::/64"),     # same /64, same key
    ("fd7a:115c:a1e0:1::1",        "fd7a:115c:a1e0:1::/64"),
    ("testclient",                 "testclient"),
    ("",                           "unknown"),
    (None,                         "unknown"),
])
def test_client_key(host, key):
    assert client_key(host) == key


# ── LoginThrottle ─────────────────────────────────────────────────────────────

def test_lockout_is_per_client():
    t = LoginThrottle(max_failures=3, lockout_seconds=900)
    for _ in range(3):
        t.record_failure("attacker")
    assert t.seconds_remaining("attacker") > 0
    assert t.seconds_remaining("operator") == 0.0
    assert t.record_failure("operator") == 0.0      # their own first strike only


def test_success_resets_only_that_client():
    t = LoginThrottle(max_failures=3)
    t.record_failure("a"); t.record_failure("a")
    t.record_failure("b"); t.record_failure("b")
    t.record_success("a")
    assert t.record_failure("a") == 0.0             # a starts over
    assert t.record_failure("b") > 0                # b's third strike


def test_global_ceiling_locks_everyone(clock):
    t = LoginThrottle(max_failures=5, lockout_seconds=900, global_max_failures=8)
    for i in range(7):
        assert t.record_failure(f"host-{i}") == 0.0
    assert t.record_failure("host-7") == 900        # 8th failure overall
    assert t.seconds_remaining("never-seen-before") == 900

    clock[0] += 901
    assert t.seconds_remaining("never-seen-before") == 0.0


def test_global_ceiling_only_counts_recent_failures(clock):
    t = LoginThrottle(max_failures=5, lockout_seconds=900, global_max_failures=8)
    for i in range(7):
        t.record_failure(f"host-{i}")
    clock[0] += 901                                 # those seven age out
    assert t.record_failure("host-x") == 0.0
    assert t.seconds_remaining("operator") == 0.0


def test_a_single_client_cannot_reach_the_default_global_ceiling(clock):
    t = LoginThrottle()                             # 5 per client, 20 global, 15 min
    for _round in range(10):
        for _ in range(5):
            t.record_failure("attacker")
        assert t.seconds_remaining("attacker") > 0
        assert t.seconds_remaining("operator") == 0.0
        clock[0] += 15 * 60 + 1


def test_failures_during_a_lockout_do_not_extend_it(clock):
    t = LoginThrottle(max_failures=2, lockout_seconds=60)
    t.record_failure("a"); t.record_failure("a")
    clock[0] += 30
    assert t.record_failure("a") == 30.0
    assert t.seconds_remaining("a") == 30.0


def test_client_table_is_bounded(clock):
    t = LoginThrottle(max_failures=5, global_max_failures=10**9, max_clients=50)
    for i in range(500):
        t.record_failure(f"10.0.{i // 250}.{i % 250}")
        clock[0] += 1
    assert len(t._clients) <= 50


# ── through the login form ────────────────────────────────────────────────────

def test_attacker_lockout_does_not_lock_out_the_operator(orch_user):
    attacker = orch_user.client("192.168.1.66")
    for _ in range(5):
        attacker.post("/login", data=BAD)
    assert attacker.post("/login", data=GOOD).status_code == 429, "attacker is locked out"

    r = orch_user.client("192.168.1.20").post("/login", data=GOOD)
    assert r.status_code == 303, "operator on another address signs in as usual"


def test_same_ipv6_network_shares_one_counter(orch_user):
    for i in range(5):
        orch_user.client(f"fd7a:115c:a1e0::{i + 1}").post("/login", data=BAD)
    r = orch_user.client("fd7a:115c:a1e0::beef").post("/login", data=GOOD)
    assert r.status_code == 429


def test_lockout_message_and_status(orch_user):
    c = orch_user.client("192.168.1.66")
    for _ in range(4):
        assert c.post("/login", data=BAD).status_code == 401
    r = c.post("/login", data=BAD)
    assert r.status_code == 429
    assert "Too many failed attempts. Try again in 15 minutes." in r.text


@pytest.fixture
def verifications(monkeypatch):
    """Count hash verifications by kind, without changing their result."""
    seen = {"real": 0, "dummy": 0}
    real, dummy = api_server.verify_password, api_server.dummy_verify

    def count_real(plain, hashed):
        seen["real"] += 1
        return real(plain, hashed)

    def count_dummy(plain):
        seen["dummy"] += 1
        return dummy(plain)

    monkeypatch.setattr(api_server, "verify_password", count_real)
    monkeypatch.setattr(api_server, "dummy_verify", count_dummy)
    return seen


def test_unknown_user_costs_one_verification_too(orch_user, verifications):
    r = orch_user.client().post("/login", data={"username": "nobody", "password": "whatever-123"})
    assert r.status_code == 401
    assert "Invalid username or password." in r.text
    assert verifications == {"real": 0, "dummy": 1}


def test_known_user_costs_exactly_one_verification(orch_user, verifications):
    assert orch_user.client().post("/login", data=BAD).status_code == 401
    assert verifications == {"real": 1, "dummy": 0}


def test_dummy_verify_never_succeeds():
    from salt_and_soil.auth import password
    password.prepare_dummy_hash()
    assert password.verify_password("", password._dummy_hash) is False
    assert password.dummy_verify("anything") is None


def test_overlong_password_is_rejected_without_hashing(orch_user, verifications):
    c = orch_user.client()
    r = c.post("/login", data={"username": TEST_USER, "password": "x" * (MAX_PASSWORD_LENGTH + 1)})
    assert r.status_code == 401
    assert verifications == {"real": 0, "dummy": 0}


def test_overlong_new_password_is_rejected(orch_user, orch):
    r = orch_user.login().post("/api/settings/password", json={
        "current_password": TEST_PASSWORD,
        "new_password": "y" * (MAX_PASSWORD_LENGTH + 1), "confirm_password": "y" * (MAX_PASSWORD_LENGTH + 1),
    })
    assert r.status_code == 400 and "at most" in r.json()["detail"]


def test_overlong_setup_password_is_rejected(orch):
    long_pw = "z" * (MAX_PASSWORD_LENGTH + 1)
    r = orch.client().post("/setup", data={
        "username": "frank", "password": long_pw, "password2": long_pw,
        "token": orch.app.state.setup_token.peek(),
    })
    assert r.status_code == 400 and "at most" in r.text
    assert not orch.auth_file.exists()
