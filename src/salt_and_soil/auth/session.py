from __future__ import annotations

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

SESSION_COOKIE    = "saltsoil_session"
REMEMBER_SECONDS  = 30 * 24 * 3600   # 30 days
SESSION_SECONDS   = 24 * 3600        # 1 day (safety cap for session-only cookies)
_SALT             = "saltsoil-user-session-v1"


def make_session_token(secret: str, username: str, pw_version: int = 0) -> str:
    s = URLSafeTimedSerializer(secret, salt=_SALT)
    return s.dumps({"u": username, "v": pw_version})


def verify_session_token(secret: str, token: str, max_age: int) -> tuple[str, int] | None:
    """Return (username, pw_version) if the token is valid, else None.

    The signing secret is server-wide, so the username can only be trusted after
    the signature checks out. The caller must still compare pw_version against the
    user's current value to reject sessions invalidated by a password change."""
    s = URLSafeTimedSerializer(secret, salt=_SALT)
    try:
        data = s.loads(token, max_age=max_age)
    except SignatureExpired:
        return None
    except BadSignature:
        return None
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    u = data.get("u")
    if not isinstance(u, str):
        return None
    v = data.get("v", 0)
    if not isinstance(v, int):
        return None
    return (u, v)
