from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

from .models import User, ALL_ALIASES
from .password import hash_password


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class UserExistsError(ValueError):
    pass


class UserNotFoundError(KeyError):
    pass


class LastAdminError(ValueError):
    """Raised when an operation would leave the system with no admin."""


class AuthStore:
    """TOML-backed multi-user auth store (data/users.toml).

    File layout::

        session_secret = "<hex>"

        [[users]]
        username        = "kattekrab"
        password_hash   = "$argon2..."
        is_admin        = true
        allowed_aliases = ["*"]
        pw_version      = 0
        created_at      = "2025-..."

    On first load, if users.toml is absent but a legacy single-user auth.toml
    exists, it is migrated automatically: that account becomes the first admin
    with access to all sources, and auth.toml is renamed to auth.toml.bak.
    """

    def __init__(self, path: Path | str, legacy_path: Path | str | None = None):
        self.path        = Path(path)
        self.legacy_path = Path(legacy_path) if legacy_path else None
        self._secret: str | None = None
        self._users:  dict[str, User] | None = None

    # ── Loading / persistence ─────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._users is not None:
            return
        if not self.path.exists() and self.legacy_path and self.legacy_path.exists():
            self._migrate_legacy()
            return
        if not self.path.exists():
            self._secret = None
            self._users  = {}
            return
        with open(self.path, "rb") as f:
            raw = tomllib.load(f)
        self._secret = raw.get("session_secret") or None
        users: dict[str, User] = {}
        for u in raw.get("users", []):
            user = User(
                username        = u["username"],
                password_hash   = u["password_hash"],
                is_admin        = bool(u.get("is_admin", False)),
                allowed_aliases = list(u.get("allowed_aliases", [])),
                pw_version      = int(u.get("pw_version", 0)),
                created_at      = u.get("created_at", ""),
            )
            users[user.username] = user
        self._users = users

    def _migrate_legacy(self) -> None:
        """Convert a legacy single-user auth.toml into the multi-user store,
        promoting the existing account to the first admin."""
        with open(self.legacy_path, "rb") as f:  # type: ignore[arg-type]
            raw = tomllib.load(f)
        admin = User(
            username        = raw["username"],
            password_hash   = raw["password_hash"],
            is_admin        = True,
            allowed_aliases = [ALL_ALIASES],
            pw_version      = 0,
            created_at      = raw.get("created_at", "") or _now_iso(),
        )
        self._secret = secrets.token_hex(32)
        self._users  = {admin.username: admin}
        self._flush()
        try:
            self.legacy_path.rename(self.legacy_path.with_suffix(self.legacy_path.suffix + ".bak"))  # type: ignore[union-attr]
        except OSError:
            pass  # migration already persisted; leaving the old file is harmless

    def _flush(self) -> None:
        """Write the in-memory store to disk. Uses json.dumps for each scalar so
        usernames/hashes with quotes or backslashes are escaped correctly."""
        assert self._users is not None
        if self._secret is None:
            self._secret = secrets.token_hex(32)
        lines = [f"session_secret = {json.dumps(self._secret)}", ""]
        for user in self._users.values():
            aliases = ", ".join(json.dumps(a) for a in user.allowed_aliases)
            lines += [
                "[[users]]",
                f"username = {json.dumps(user.username)}",
                f"password_hash = {json.dumps(user.password_hash)}",
                f"is_admin = {'true' if user.is_admin else 'false'}",
                f"allowed_aliases = [{aliases}]",
                f"pw_version = {user.pw_version}",
                f"created_at = {json.dumps(user.created_at)}",
                "",
            ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(lines), encoding="utf-8")

    def invalidate(self) -> None:
        self._secret = None
        self._users  = None

    # ── Queries ───────────────────────────────────────────────────────────────

    def exists(self) -> bool:
        """True once at least one user account exists (the app is past setup)."""
        self._ensure_loaded()
        return bool(self._users)

    def session_secret(self) -> str:
        self._ensure_loaded()
        if self._secret is None:
            self._secret = secrets.token_hex(32)
            if self._users:
                self._flush()
        return self._secret

    def get(self, username: str) -> User | None:
        self._ensure_loaded()
        assert self._users is not None
        return self._users.get(username)

    def list(self) -> list[User]:
        self._ensure_loaded()
        assert self._users is not None
        return list(self._users.values())

    def admin_count(self) -> int:
        self._ensure_loaded()
        assert self._users is not None
        return sum(1 for u in self._users.values() if u.is_admin)

    # ── Mutations ─────────────────────────────────────────────────────────────

    def create(
        self,
        username: str,
        plain_password: str,
        is_admin: bool = False,
        allowed_aliases: list[str] | None = None,
    ) -> User:
        self._ensure_loaded()
        assert self._users is not None
        if username in self._users:
            raise UserExistsError(f"User '{username}' already exists")
        user = User(
            username        = username,
            password_hash   = hash_password(plain_password),
            is_admin        = is_admin,
            allowed_aliases = [ALL_ALIASES] if is_admin else list(allowed_aliases or []),
            pw_version      = 0,
            created_at      = _now_iso(),
        )
        self._users[username] = user
        self._flush()
        return user

    def set_rights(self, username: str, is_admin: bool, allowed_aliases: list[str]) -> User:
        self._ensure_loaded()
        assert self._users is not None
        user = self._users.get(username)
        if not user:
            raise UserNotFoundError(username)
        # Block demoting the last remaining admin.
        if user.is_admin and not is_admin and self.admin_count() <= 1:
            raise LastAdminError("Cannot remove the last admin")
        user.is_admin        = is_admin
        user.allowed_aliases = [ALL_ALIASES] if is_admin else list(allowed_aliases)
        self._flush()
        return user

    def set_password(self, username: str, new_plain_password: str) -> User:
        """Set a user's password (admin action — no current password needed).
        Bumps pw_version, invalidating that user's existing sessions."""
        self._ensure_loaded()
        assert self._users is not None
        user = self._users.get(username)
        if not user:
            raise UserNotFoundError(username)
        user.password_hash = hash_password(new_plain_password)
        user.pw_version   += 1
        self._flush()
        return user

    def delete(self, username: str) -> None:
        self._ensure_loaded()
        assert self._users is not None
        user = self._users.get(username)
        if not user:
            raise UserNotFoundError(username)
        if user.is_admin and self.admin_count() <= 1:
            raise LastAdminError("Cannot delete the last admin")
        del self._users[username]
        self._flush()
