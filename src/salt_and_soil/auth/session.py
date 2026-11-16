from __future__ import annotations

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

SESSION_COOKIE    = "saltsoil_session"
REMEMBER_SECONDS  = 30 * 24 * 3600   # sessions started with "Remember me"
SESSION_SECONDS   = 24 * 3600        # every other session
_SALT             = "saltsoil-user-session-v1"


def make_session_token(secret: str, username: str, remember: bool = False) -> str:
    s = URLSafeTimedSerializer(secret, salt=_SALT)
    payload: dict = {"u": username}
    if remember:
        payload["r"] = 1
    return s.dumps(payload)


def verify_session_token(secret: str, token: str, max_age: int | None = None) -> str | None:
    """The user name if `token` is valid and not expired, else None.

    How long a token lives is decided by the token itself: REMEMBER_SECONDS
    if it was issued with "Remember me", SESSION_SECONDS otherwise (this
    includes tokens from before the flag existed). `max_age` can only
    shorten that."""
    s = URLSafeTimedSerializer(secret, salt=_SALT)
    try:
        data = s.loads(token, max_age=REMEMBER_SECONDS)      # signature + outer limit
        remember = isinstance(data, dict) and bool(data.get("r"))
        limit = REMEMBER_SECONDS if remember else SESSION_SECONDS
        if max_age is not None:
            limit = min(limit, max_age)
        data = s.loads(token, max_age=limit)
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
