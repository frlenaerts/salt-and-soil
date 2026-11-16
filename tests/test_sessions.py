"""
test_sessions.py — sessions expire when they should and logging out really
ends them.

Before: every token was accepted for 30 days whether or not "Remember me"
was ticked, and logout only removed the cookie from that one browser — a
copied cookie kept working.
"""
import stat
import time
import types

import itsdangerous.timed as timed
import pytest

from salt_and_soil.auth.password import verify_password
from salt_and_soil.auth.session import (
    REMEMBER_SECONDS, SESSION_COOKIE, SESSION_SECONDS,
    make_session_token, verify_session_token,
)
from salt_and_soil.auth.store import AuthStore

from conftest import TEST_PASSWORD

SECRET = "a" * 64
HOUR, DAY = 3600, 24 * 3600


def _token_issued(seconds_ago: int, monkeypatch, **kwargs) -> str:
    """A token signed `seconds_ago` in the past (itsdangerous reads the clock
    through its own `time` module reference)."""
    past = time.time() - seconds_ago
    with monkeypatch.context() as m:
        m.setattr(timed, "time", types.SimpleNamespace(time=lambda: past))
        return make_session_token(SECRET, "frank", **kwargs)


# ── token lifetime ────────────────────────────────────────────────────────────

def test_lifetimes():
    assert SESSION_SECONDS == DAY
    assert REMEMBER_SECONDS == 30 * DAY


@pytest.mark.parametrize("age, valid", [(0, True), (23 * HOUR, True), (25 * HOUR, False), (29 * DAY, False)])
def test_plain_session_lasts_a_day(monkeypatch, age, valid):
    token = _token_issued(age, monkeypatch)
    assert (verify_session_token(SECRET, token) == "frank") is valid


@pytest.mark.parametrize("age, valid", [(25 * HOUR, True), (29 * DAY, True), (31 * DAY, False)])
def test_remembered_session_lasts_thirty_days(monkeypatch, age, valid):
    token = _token_issued(age, monkeypatch, remember=True)
    assert (verify_session_token(SECRET, token) == "frank") is valid


def test_max_age_can_shorten_but_not_extend(monkeypatch):
    token = _token_issued(2 * HOUR, monkeypatch)
    assert verify_session_token(SECRET, token, max_age=HOUR) is None
    old = _token_issued(25 * HOUR, monkeypatch)
    assert verify_session_token(SECRET, old, max_age=30 * DAY) is None


def test_remember_flag_cannot_be_added_without_the_secret(monkeypatch):
    from itsdangerous import URLSafeTimedSerializer
    forged = URLSafeTimedSerializer("b" * 64, salt="saltsoil-user-session-v1").dumps({"u": "frank", "r": 1})
    assert verify_session_token(SECRET, forged) is None


# ── cookies issued by the app ─────────────────────────────────────────────────

def _set_cookie(response) -> str:
    return response.headers["set-cookie"].lower()


def test_login_without_remember_gives_a_browser_session_cookie(orch_user):
    r = orch_user.client().post("/login", data={"username": "admin", "password": TEST_PASSWORD})
    assert "max-age" not in _set_cookie(r)
    assert "httponly" in _set_cookie(r) and "samesite=lax" in _set_cookie(r)


def test_login_with_remember_gives_a_thirty_day_cookie(orch_user):
    r = orch_user.client().post("/login", data={"username": "admin", "password": TEST_PASSWORD, "remember": "1"})
    assert f"max-age={REMEMBER_SECONDS}" in _set_cookie(r)


def test_app_rejects_a_day_old_plain_session(orch_user, monkeypatch):
    secret = AuthStore(orch_user.auth_file).load().session_secret
    past = time.time() - 25 * HOUR
    with monkeypatch.context() as m:
        m.setattr(timed, "time", types.SimpleNamespace(time=lambda: past))
        stale    = make_session_token(secret, "admin")
        remember = make_session_token(secret, "admin", remember=True)

    c = orch_user.client(); c.cookies.set(SESSION_COOKIE, stale)
    assert c.get("/api/state").status_code == 401
    c = orch_user.client(); c.cookies.set(SESSION_COOKIE, remember)
    assert c.get("/api/state").status_code == 200


# ── logout ────────────────────────────────────────────────────────────────────

def _replay(orch_user, cookie: str) -> int:
    c = orch_user.client()
    c.cookies.set(SESSION_COOKIE, cookie)
    return c.get("/api/state").status_code


def test_logout_invalidates_the_session_on_the_server(orch_user):
    c = orch_user.login()
    cookie = c.cookies[SESSION_COOKIE]
    assert _replay(orch_user, cookie) == 200

    r = c.post("/logout")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert _replay(orch_user, cookie) == 401, "a copied cookie must stop working at logout"


def test_logout_ends_every_session(orch_user):
    laptop, phone = orch_user.login(), orch_user.login(remember=True)
    phone_cookie = phone.cookies[SESSION_COOKIE]
    laptop.post("/logout")
    assert _replay(orch_user, phone_cookie) == 401


def test_logout_keeps_the_password_and_file_mode(orch_user):
    orch_user.login().post("/logout")
    user = AuthStore(orch_user.auth_file).load()
    assert verify_password(TEST_PASSWORD, user.password_hash)
    assert stat.S_IMODE(orch_user.auth_file.stat().st_mode) == 0o600
    assert orch_user.login().get("/api/state").status_code == 200


def test_anonymous_logout_does_not_sign_others_out(orch_user):
    cookie = orch_user.login().cookies[SESSION_COOKIE]
    assert orch_user.client().post("/logout").status_code == 303     # no session
    stranger = orch_user.client(); stranger.cookies.set(SESSION_COOKIE, "garbage")
    assert stranger.post("/logout").status_code == 303               # invalid session
    assert _replay(orch_user, cookie) == 200


def test_logout_by_get_is_gone(orch_user):
    c = orch_user.login()
    cookie = c.cookies[SESSION_COOKIE]
    assert c.get("/logout").status_code == 405
    assert _replay(orch_user, cookie) == 200


def test_password_change_ends_other_sessions_but_keeps_this_one(orch_user):
    laptop, phone = orch_user.login(), orch_user.login()
    phone_cookie = phone.cookies[SESSION_COOKIE]
    r = laptop.post("/api/settings/password", json={
        "current_password": TEST_PASSWORD, "new_password": "brand-new-pass-1", "confirm_password": "brand-new-pass-1"})
    assert r.status_code == 200
    assert laptop.get("/api/state").status_code == 200
    assert _replay(orch_user, phone_cookie) == 401
