"""Smoke tests: every HTML page renders through the real template engine.

Guards the template call signature across FastAPI/Starlette upgrades — a
broken call only shows up when a page is actually requested.
"""
from salt_and_soil import __version__


def test_setup_page_renders_on_first_run(orch):
    r = orch.client().get("/setup")
    assert r.status_code == 200
    assert "Create your account" in r.text


def test_first_visit_redirects_to_setup(orch):
    r = orch.client().get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/setup"


def test_login_page_renders(orch_user):
    r = orch_user.client().get("/login")
    assert r.status_code == 200
    assert "Sign in" in r.text


def test_failed_login_rerenders_form_with_error(orch_user):
    r = orch_user.client().post("/login", data={"username": "admin", "password": "wrong-password"})
    assert r.status_code == 401
    assert "Invalid username or password." in r.text


def test_index_renders_for_signed_in_user(orch_user):
    r = orch_user.login().get("/")
    assert r.status_code == 200
    assert f"v{__version__}" in r.text
    assert 'value="admin"' in r.text


def test_api_requires_a_session(orch_user):
    r = orch_user.client().get("/api/state")
    assert r.status_code == 401
