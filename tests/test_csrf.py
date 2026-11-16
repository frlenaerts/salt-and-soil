"""
test_csrf.py — state-changing requests are only accepted from the UI's own
origin, and JSON endpoints only accept real JSON requests.

SameSite=Lax keeps other sites out, but not other origins on the same site
(another port on the same host, a sibling host name on the LAN).
"""
import pytest

from conftest import make_orchestrator
from salt_and_soil.shared.enums import AppStatus
from salt_and_soil.transport.api_server import is_cross_origin

SCHEDULE = {"enabled": True, "days": [0, 1], "hour": 4, "minute": 30}


# ── the decision function ─────────────────────────────────────────────────────

@pytest.mark.parametrize("headers, expected", [
    # No browser signal at all (curl, scripts): nothing to judge.
    ({"host": "nas:8080"},                                                   False),
    # Origin vs Host — the plain-HTTP LAN case.
    ({"host": "nas:8080", "origin": "http://nas:8080"},                      False),
    ({"host": "NAS:8080", "origin": "http://nas:8080"},                      False),
    ({"host": "nas",      "origin": "http://nas"},                           False),
    ({"host": "nas:8080", "origin": "http://nas:9000"},                      True),   # other port, same site
    ({"host": "nas:8080", "origin": "http://cam.home.lan"},                  True),
    ({"host": "nas:8080", "origin": "null"},                                 True),   # sandboxed frame, file://
    ({"host": "nas:8080", "origin": ""},                                     True),
    # Sec-Fetch-Site wins when the browser sends it (HTTPS, localhost).
    ({"host": "127.0.0.1:8080", "origin": "https://salt.example", "sec-fetch-site": "same-origin"}, False),
    ({"host": "nas:8080", "sec-fetch-site": "none"},                         False),
    ({"host": "nas:8080", "origin": "http://nas:8080", "sec-fetch-site": "same-site"},  True),
    ({"host": "nas:8080", "origin": "http://nas:8080", "sec-fetch-site": "cross-site"}, True),
])
def test_is_cross_origin(headers, expected):
    assert is_cross_origin(headers) is expected


def test_allowed_origins_is_an_explicit_escape_hatch():
    headers = {"host": "127.0.0.1:8080", "origin": "https://Salt.Example.com"}
    assert is_cross_origin(headers) is True
    assert is_cross_origin(headers, {"https://salt.example.com"}) is False


# ── through the app ───────────────────────────────────────────────────────────

def test_same_origin_post_is_accepted(orch_user):
    c = orch_user.login()
    r = c.post("/api/schedule", json=SCHEDULE, headers={"Origin": "http://testserver"})
    assert r.status_code == 200
    assert r.json() == SCHEDULE


def test_post_without_origin_is_accepted(orch_user):
    r = orch_user.login().post("/api/schedule", json=SCHEDULE)
    assert r.status_code == 200


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "http://testserver:9000"},
    {"Origin": "null"},
    {"Origin": "http://testserver", "Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_cross_origin_post_is_rejected_and_changes_nothing(orch_user, headers):
    c = orch_user.login()
    r = c.post("/api/schedule", json=SCHEDULE, headers=headers)
    assert r.status_code == 403
    assert r.json() == {"error": "cross-origin request rejected"}
    assert orch_user.rt.get_schedule().enabled is False


@pytest.mark.parametrize("path", ["/api/start", "/api/reset", "/api/cancel", "/api/log/clear", "/api/execute"])
def test_every_state_changing_endpoint_is_covered(orch_user, path):
    r = orch_user.login().post(path, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_cross_origin_login_is_rejected_before_credentials_are_checked(orch_user):
    c = orch_user.client()
    r = c.post("/login", data={"username": "admin", "password": "correct-horse-battery"},
               headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert "saltsoil_session" not in r.cookies


def test_cross_origin_get_is_not_blocked(orch_user):
    # Reads are harmless: without CORS headers the other origin cannot see the answer.
    r = orch_user.login().get("/api/state", headers={"Origin": "http://evil.example"})
    assert r.status_code == 200


def test_configured_origin_is_accepted(tmp_path):
    from salt_and_soil.auth.store import AuthStore
    o = make_orchestrator(tmp_path, extra_config="""
        [server]
        allowed_origins = ["https://salt.example.com/"]
    """)
    AuthStore(o.auth_file).create("admin", "correct-horse-battery")
    c = o.client()
    headers = {"Origin": "https://salt.example.com"}
    assert c.post("/login", data={"username": "admin", "password": "correct-horse-battery"},
                  headers=headers).status_code == 303
    assert c.post("/api/schedule", json=SCHEDULE, headers=headers).status_code == 200


@pytest.mark.parametrize("value", ['"https://a.example"', '["salt.example.com"]', '["https://a.example/path"]', "[5]"])
def test_malformed_allowed_origins_rejected(tmp_path, value):
    with pytest.raises(ValueError, match="allowed_origins"):
        make_orchestrator(tmp_path, extra_config=f"""
            [server]
            allowed_origins = {value}
        """)


# ── JSON endpoints ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/schedule", "/api/execute", "/api/settings/password"])
def test_json_endpoints_reject_form_style_bodies(orch_user, path):
    # What an HTML form with enctype=text/plain can produce.
    orch_user.rt.status = AppStatus.READY      # so /api/execute gets as far as its body
    r = orch_user.login().post(path, content='{"enabled":true,"days":[0],"x":"="}',
                               headers={"Content-Type": "text/plain"})
    assert r.status_code == 415


@pytest.mark.parametrize("body", ["[1,2]", '"text"', "{not json"])
def test_json_endpoints_reject_non_object_bodies(orch_user, body):
    r = orch_user.login().post("/api/schedule", content=body,
                               headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_json_content_type_with_charset_is_accepted(orch_user):
    import json
    r = orch_user.login().post("/api/schedule", content=json.dumps(SCHEDULE),
                               headers={"Content-Type": "application/json; charset=utf-8"})
    assert r.status_code == 200
