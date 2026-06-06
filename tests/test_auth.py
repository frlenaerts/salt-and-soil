"""Unit tests for the auth module (password + store + session)."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from salt_and_soil.auth.password import hash_password, verify_password, MIN_PASSWORD_LENGTH
from salt_and_soil.auth.store import (
    AuthStore, UserExistsError, UserNotFoundError, LastAdminError,
)
from salt_and_soil.auth.session import make_session_token, verify_session_token
from salt_and_soil.auth.throttle import LoginThrottle


def test_hash_then_verify_roundtrip():
    h = hash_password("correcthorsebattery")
    assert verify_password("correcthorsebattery", h) is True
    assert verify_password("wrong", h) is False


def test_verify_rejects_garbage_hash():
    assert verify_password("x", "not-an-argon2-hash") is False


def test_min_password_length_is_8():
    assert MIN_PASSWORD_LENGTH == 8


# ── Store: create / load ────────────────────────────────────────────────────────

def test_store_create_and_load(tmp_path):
    store = AuthStore(tmp_path / "users.toml")
    assert store.exists() is False

    user = store.create("frank", "supersecret", is_admin=True)
    assert store.exists() is True
    assert user.username == "frank"
    assert user.is_admin is True
    assert user.allowed_aliases == ["*"]
    assert user.password_hash.startswith("$argon2")
    assert user.created_at  # non-empty

    secret = store.session_secret()
    assert len(secret) >= 32

    # Reload from disk — invalidate cache to force read
    store.invalidate()
    loaded = store.get("frank")
    assert loaded is not None
    assert loaded.username == "frank"
    assert loaded.password_hash == user.password_hash
    assert loaded.is_admin is True
    assert loaded.allowed_aliases == ["*"]
    assert store.session_secret() == secret  # stable across reload


def test_store_create_non_admin_with_aliases(tmp_path):
    store = AuthStore(tmp_path / "users.toml")
    u = store.create("bob", "password1", is_admin=False, allowed_aliases=["Projects"])
    assert u.is_admin is False
    assert u.allowed_aliases == ["Projects"]
    assert u.can_access("Projects") is True
    assert u.can_access("Photos") is False
    assert u.has_all_access is False


def test_store_duplicate_user_rejected(tmp_path):
    store = AuthStore(tmp_path / "users.toml")
    store.create("frank", "password1")
    with pytest.raises(UserExistsError):
        store.create("frank", "password2")


def test_store_set_password_bumps_version_and_hash(tmp_path):
    store = AuthStore(tmp_path / "users.toml")
    original = store.create("frank", "oldpassword")
    old_version, old_hash = original.pw_version, original.password_hash
    updated  = store.set_password("frank", "newpassword")

    assert updated.username == "frank"
    assert updated.pw_version == old_version + 1
    assert updated.password_hash != old_hash
    assert verify_password("newpassword", updated.password_hash) is True
    assert verify_password("oldpassword", updated.password_hash) is False


def test_store_set_password_unknown_user(tmp_path):
    store = AuthStore(tmp_path / "users.toml")
    store.create("frank", "password1")
    with pytest.raises(UserNotFoundError):
        store.set_password("nobody", "whatever1")


def test_store_set_rights_and_promote(tmp_path):
    store = AuthStore(tmp_path / "users.toml")
    store.create("admin", "password1", is_admin=True)
    store.create("bob", "password1", allowed_aliases=["A"])

    bob = store.set_rights("bob", is_admin=False, allowed_aliases=["A", "B"])
    assert bob.allowed_aliases == ["A", "B"]
    assert bob.is_admin is False

    # Promoting to admin grants all access regardless of submitted aliases.
    bob = store.set_rights("bob", is_admin=True, allowed_aliases=[])
    assert bob.is_admin is True
    assert bob.allowed_aliases == ["*"]


def test_store_last_admin_protected_on_delete_and_demote(tmp_path):
    store = AuthStore(tmp_path / "users.toml")
    store.create("admin", "password1", is_admin=True)
    with pytest.raises(LastAdminError):
        store.delete("admin")
    with pytest.raises(LastAdminError):
        store.set_rights("admin", is_admin=False, allowed_aliases=[])

    # With a second admin, demoting the first is allowed.
    store.create("admin2", "password1", is_admin=True)
    demoted = store.set_rights("admin", is_admin=False, allowed_aliases=["A"])
    assert demoted.is_admin is False


def test_store_delete(tmp_path):
    store = AuthStore(tmp_path / "users.toml")
    store.create("admin", "password1", is_admin=True)
    store.create("bob", "password1")
    store.delete("bob")
    assert store.get("bob") is None
    assert len(store.list()) == 1


def test_store_handles_unicode_and_quotes_in_username(tmp_path):
    # Persist via TOML — ensure escaping in save/load works.
    store = AuthStore(tmp_path / "users.toml")
    store.create('weird"user\\name', "password1")
    store.invalidate()
    loaded = store.get('weird"user\\name')
    assert loaded is not None
    assert loaded.username == 'weird"user\\name'


# ── Store: legacy migration ─────────────────────────────────────────────────────

def test_legacy_auth_toml_migrates_to_first_admin(tmp_path):
    legacy = tmp_path / "auth.toml"
    legacy.write_text(
        f'username = {json.dumps("kattekrab")}\n'
        f'password_hash = {json.dumps(hash_password("hunter2pw"))}\n'
        f'session_secret = {json.dumps("a" * 64)}\n'
        f'created_at = "2025-01-01T00:00:00Z"\n',
        encoding="utf-8",
    )
    users = tmp_path / "users.toml"
    store = AuthStore(users, legacy_path=legacy)

    assert store.exists() is True
    u = store.get("kattekrab")
    assert u is not None
    assert u.is_admin is True
    assert u.allowed_aliases == ["*"]
    assert u.pw_version == 0
    assert verify_password("hunter2pw", u.password_hash) is True

    # users.toml written, legacy renamed to .bak
    assert users.exists()
    assert (tmp_path / "auth.toml.bak").exists()
    assert not legacy.exists()

    # Idempotent: a fresh store over the same users.toml does not re-migrate.
    store2 = AuthStore(users, legacy_path=legacy)
    assert store2.get("kattekrab") is not None


# ── Session tokens ──────────────────────────────────────────────────────────────

def test_session_token_roundtrip():
    secret = "a" * 64
    token = make_session_token(secret, "frank", 0)
    assert verify_session_token(secret, token, max_age=60) == ("frank", 0)


def test_session_token_carries_pw_version():
    secret = "a" * 64
    token = make_session_token(secret, "frank", 7)
    assert verify_session_token(secret, token, max_age=60) == ("frank", 7)


def test_session_token_rejects_wrong_secret():
    token = make_session_token("a" * 64, "frank", 0)
    assert verify_session_token("b" * 64, token, max_age=60) is None


def test_session_token_rejects_garbage():
    assert verify_session_token("a" * 64, "garbage.token.data", max_age=60) is None


def test_session_token_respects_max_age(monkeypatch):
    import types
    import itsdangerous.timed as timed
    secret = "a" * 64

    # itsdangerous.timed uses `time.time()` via its `time` module reference.
    # Substitute a stand-in module whose `.time()` returns a fixed past instant
    # during signing, then restore the real module before verifying.
    past = time.time() - 120
    monkeypatch.setattr(timed, "time", types.SimpleNamespace(time=lambda: past))
    token = make_session_token(secret, "frank", 0)
    monkeypatch.undo()

    assert verify_session_token(secret, token, max_age=60)  is None
    assert verify_session_token(secret, token, max_age=300) == ("frank", 0)


# ── Throttle ────────────────────────────────────────────────────────────────────

def test_throttle_unlocked_by_default():
    t = LoginThrottle()
    assert t.seconds_remaining() == 0.0


def test_throttle_locks_after_max_failures():
    t = LoginThrottle(max_failures=3, lockout_seconds=900)
    assert t.record_failure() == 0.0   # 1st
    assert t.record_failure() == 0.0   # 2nd
    remaining = t.record_failure()     # 3rd → locks
    assert remaining == 900
    assert t.seconds_remaining() > 0


def test_throttle_success_resets_counter():
    t = LoginThrottle(max_failures=3, lockout_seconds=900)
    t.record_failure()
    t.record_failure()
    t.record_success()
    # After reset, two more failures should NOT trigger lockout (counter restarted).
    assert t.record_failure() == 0.0
    assert t.record_failure() == 0.0
    assert t.seconds_remaining() == 0.0


def test_throttle_unlocks_after_timeout(monkeypatch):
    import salt_and_soil.auth.throttle as throttle_mod
    fake_now = [1000.0]
    monkeypatch.setattr(throttle_mod.time, "monotonic", lambda: fake_now[0])

    t = LoginThrottle(max_failures=2, lockout_seconds=60)
    t.record_failure()
    t.record_failure()
    assert t.seconds_remaining() == 60.0

    fake_now[0] += 61
    assert t.seconds_remaining() == 0.0
    # Counter should also reset after lockout expires — next failure starts fresh.
    assert t.record_failure() == 0.0
