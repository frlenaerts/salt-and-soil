from __future__ import annotations

import json
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

from .models import AuthUser
from .password import hash_password
from ..shared.paths import write_private


class AuthStore:
    """TOML-backed single-user auth store (data/auth.toml)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._cache: AuthUser | None = None

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> AuthUser:
        if self._cache is None:
            self._make_private()
            with open(self.path, "rb") as f:
                raw = tomllib.load(f)
            self._cache = AuthUser(
                username       = raw["username"],
                password_hash  = raw["password_hash"],
                session_secret = raw["session_secret"],
                created_at     = raw.get("created_at", ""),
            )
        return self._cache

    def _make_private(self) -> None:
        """Tighten a file written by an older version (default umask, 0644).
        It holds the password hash and the session-signing secret."""
        try:
            if stat.S_IMODE(self.path.stat().st_mode) & 0o077:
                self.path.chmod(0o600)
        except OSError:
            pass

    def reload(self) -> AuthUser:
        self._cache = None
        return self.load()

    def invalidate(self) -> None:
        self._cache = None

    def save(self, user: AuthUser) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"username = {json.dumps(user.username)}",
            f"password_hash = {json.dumps(user.password_hash)}",
            f"session_secret = {json.dumps(user.session_secret)}",
            f"created_at = {json.dumps(user.created_at)}",
            "",
        ]
        write_private(self.path, "\n".join(lines))
        self._cache = user

    def create(self, username: str, plain_password: str) -> AuthUser:
        user = AuthUser(
            username       = username,
            password_hash  = hash_password(plain_password),
            session_secret = secrets.token_hex(32),
            created_at     = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        self.save(user)
        return user

    def change_password(self, new_plain_password: str) -> AuthUser:
        """Rotate password + session_secret (invalidates all existing sessions)."""
        current = self.load()
        updated = AuthUser(
            username       = current.username,
            password_hash  = hash_password(new_plain_password),
            session_secret = secrets.token_hex(32),
            created_at     = current.created_at,
        )
        self.save(updated)
        return updated
