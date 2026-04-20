"""Unit tests for the auth module (password + store + session)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from salt_and_soil.auth.password import hash_password, verify_password, MIN_PASSWORD_LENGTH
from salt_and_soil.auth.store import AuthStore
from salt_and_soil.auth.session import make_session_token, verify_session_token


def test_hash_then_verify_roundtrip():
    h = hash_password("correcthorsebattery")
    assert verify_password("correcthorsebattery", h) is True
    assert verify_password("wrong", h) is False


def test_verify_rejects_garbage_hash():
    assert verify_password("x", "not-an-argon2-hash") is False


def test_min_password_length_is_8():
    assert MIN_PASSWORD_LENGTH == 8


def test_store_create_and_load(tmp_path):
    store = AuthStore(tmp_path / "auth.toml")
    assert store.exists() is False

    user = store.create("frank", "supersecret")
    assert store.exists() is True
    assert user.username == "frank"
    assert user.password_hash.startswith("$argon2")
    assert len(user.session_secret) >= 32
    assert user.created_at  # non-empty

    # Reload from disk — invalidate cache to force read
    store.invalidate()
    loaded = store.load()
    assert loaded.username == "frank"
    assert loaded.password_hash == user.password_hash
    assert loaded.session_secret == user.session_secret


def test_store_change_password_rotates_secret_and_hash(tmp_path):
    store = AuthStore(tmp_path / "auth.toml")
    original = store.create("frank", "oldpassword")
    updated  = store.change_password("newpassword")

    assert updated.username == original.username
    assert updated.password_hash != original.password_hash
    assert updated.session_secret != original.session_secret
    assert verify_password("newpassword", updated.password_hash) is True
    assert verify_password("oldpassword", updated.password_hash) is False


def test_store_handles_unicode_and_quotes_in_username(tmp_path):
    # Persist via TOML — ensure escaping in save/load works.
    store = AuthStore(tmp_path / "auth.toml")
    store.create('weird"user\\name', "password1")
    store.invalidate()
    loaded = store.load()
    assert loaded.username == 'weird"user\\name'


def test_session_token_roundtrip():
    secret = "a" * 64
    token = make_session_token(secret, "frank")
    assert verify_session_token(secret, token, max_age=60) == "frank"


def test_session_token_rejects_wrong_secret():
    token = make_session_token("a" * 64, "frank")
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
    token = make_session_token(secret, "frank")
    monkeypatch.undo()

    assert verify_session_token(secret, token, max_age=60)  is None
    assert verify_session_token(secret, token, max_age=300) == "frank"
