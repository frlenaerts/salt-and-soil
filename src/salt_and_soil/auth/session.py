from __future__ import annotations

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

SESSION_COOKIE    = "saltsoil_session"
REMEMBER_SECONDS  = 30 * 24 * 3600   # 30 days
SESSION_SECONDS   = 24 * 3600        # 1 day (safety cap for session-only cookies)
_SALT             = "saltsoil-user-session-v1"


def make_session_token(secret: str, username: str) -> str:
    s = URLSafeTimedSerializer(secret, salt=_SALT)
    return s.dumps({"u": username})


def verify_session_token(secret: str, token: str, max_age: int) -> str | None:
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
    return u if isinstance(u, str) else None
