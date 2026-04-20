from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

from .models import AuthUser
from .password import hash_password


class AuthStore:
    """TOML-backed single-user auth store (data/auth.toml)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._cache: AuthUser | None = None

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> AuthUser:
        if self._cache is None:
            with open(self.path, "rb") as f:
                raw = tomllib.load(f)
            self._cache = AuthUser(
                username       = raw["username"],
                password_hash  = raw["password_hash"],
                session_secret = raw["session_secret"],
                created_at     = raw.get("created_at", ""),
            )
        return self._cache

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
        self.path.write_text("\n".join(lines), encoding="utf-8")
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
