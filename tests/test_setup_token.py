"""
test_setup_token.py — creating the first account needs a token from the log.

Without it, whoever reaches the port first on a fresh install (or after
auth.toml went missing) owns the orchestrator.
"""
import logging
import re

import pytest

from conftest import make_orchestrator
from salt_and_soil.auth.setup_token import SetupToken

FORM = {"username": "frank", "password": "supersecret1", "password2": "supersecret1"}
TOKEN_RE = re.compile(r"^[A-HJ-NP-Z2-9]{4}(-[A-HJ-NP-Z2-9]{4}){3}$")


@pytest.fixture
def app_log():
    """Lines written to the application log (it does not propagate to root)."""
    records: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler, logger = Collect(), logging.getLogger("salt-and-soil")
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)


def _token(o) -> str:
    return o.app.state.setup_token.peek()


# ── the token object ──────────────────────────────────────────────────────────

def test_token_format_is_typable_and_random():
    a, b = SetupToken().current(), SetupToken().current()
    assert TOKEN_RE.match(a) and TOKEN_RE.match(b)
    assert a != b


def test_token_is_stable_until_cleared():
    t = SetupToken()
    first = t.current()
    assert t.current() == first
    t.clear()
    assert t.peek() is None
    assert t.matches(first) is False


def test_token_matching_is_forgiving_about_form_but_not_content():
    t = SetupToken()
    value = t.current()
    assert t.matches(value)
    assert t.matches(value.lower())
    assert t.matches(value.replace("-", ""))
    assert t.matches(f"  {value.replace('-', ' ')} ")
    assert not t.matches("")
    assert not t.matches(value[:-1])
    assert not t.matches(value[:-1] + ("A" if value[-1] != "A" else "B"))


def test_nothing_matches_when_no_token_was_issued():
    assert SetupToken().matches("") is False


# ── first run ─────────────────────────────────────────────────────────────────

def test_token_is_generated_and_logged_at_startup(tmp_path, app_log):
    o = make_orchestrator(tmp_path)
    assert TOKEN_RE.match(_token(o))
    assert sum(_token(o) in line for line in app_log) == 1
    assert any("/setup" in line and "setup token" in line for line in app_log)


@pytest.mark.parametrize("token", [None, "", "AAAA-AAAA-AAAA-AAAA", "x"])
def test_setup_without_the_token_creates_nothing(orch, token):
    data = dict(FORM) if token is None else dict(FORM, token=token)
    r = orch.client().post("/setup", data=data)
    assert r.status_code == 400
    assert "Invalid setup token" in r.text
    assert "saltsoil_session" not in r.cookies
    assert not orch.auth_file.exists()


def test_setup_with_the_token_creates_the_account_and_signs_in(orch):
    c = orch.client()
    r = c.post("/setup", data=dict(FORM, token=_token(orch)))
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert orch.auth_file.exists()
    assert c.get("/api/state").status_code == 200


def test_token_is_single_use(orch):
    token = _token(orch)
    assert orch.client().post("/setup", data=dict(FORM, token=token)).status_code == 303
    assert _token(orch) is None
    # A second attempt cannot create or replace anything.
    r = orch.client().post("/setup", data=dict(FORM, username="mallory", token=token))
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert 'username = "frank"' in orch.auth_file.read_text()


def test_token_survives_a_form_mistake(orch):
    token = _token(orch)
    r = orch.client().post("/setup", data=dict(FORM, password2="different", token=token))
    assert r.status_code == 400
    assert "Passwords do not match." in r.text
    assert f'value="{token}"' in r.text, "valid token is handed back so it need not be retyped"
    assert _token(orch) == token


def test_setup_page_never_reveals_the_token(orch):
    r = orch.client().get("/setup")
    assert r.status_code == 200
    assert _token(orch) not in r.text
    assert "journalctl" in r.text


# ── existing installs ─────────────────────────────────────────────────────────

def test_no_token_when_an_account_exists(orch_user, app_log):
    assert _token(orch_user) is None
    r = orch_user.client().post("/setup", data=dict(FORM, token="AAAA-AAAA-AAAA-AAAA"))
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_missing_auth_file_reopens_setup_behind_a_fresh_token(orch_user, app_log):
    orch_user.auth_file.unlink()                       # "delete that file to start over"
    assert _token(orch_user) is None

    c = orch_user.client()
    assert c.post("/setup", data=dict(FORM, token="")).status_code == 400
    assert not orch_user.auth_file.exists()

    token = _token(orch_user)
    assert TOKEN_RE.match(token)
    assert any(token in line for line in app_log), "the new token must reach the log"
    assert c.post("/setup", data=dict(FORM, token=token)).status_code == 303
