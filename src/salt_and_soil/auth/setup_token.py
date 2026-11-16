from __future__ import annotations

import hmac
import logging
import secrets

log = logging.getLogger("salt-and-soil")

# No 0/O, 1/I: the token is read from a log and may be typed by hand.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_GROUPS, _GROUP_LEN = 4, 4          # XXXX-XXXX-XXXX-XXXX → 80 bits


class SetupToken:
    """One-time proof that whoever fills in /setup can read the service log.

    Without it, the first person to reach the port of a fresh install — or of
    an install whose auth.toml went missing — could create the account. The
    token lives in memory only, is written to the log when it is generated,
    and is discarded once the account exists."""

    def __init__(self) -> None:
        self._value: str | None = None

    def current(self) -> str:
        """The active token; generates and logs one if there is none."""
        if self._value is None:
            chars = "".join(secrets.choice(_ALPHABET) for _ in range(_GROUPS * _GROUP_LEN))
            self._value = "-".join(
                chars[i:i + _GROUP_LEN] for i in range(0, len(chars), _GROUP_LEN)
            )
            log.warning(
                "No user account yet. Open /setup in a browser and enter this "
                "one-time setup token: %s", self._value,
            )
        return self._value

    def peek(self) -> str | None:
        """The active token, or None — never generates one."""
        return self._value

    def matches(self, candidate: str) -> bool:
        """Constant-time comparison; ignores case, dashes and whitespace."""
        if self._value is None or not isinstance(candidate, str):
            return False
        return hmac.compare_digest(_canonical(candidate), _canonical(self._value))

    def clear(self) -> None:
        self._value = None


def _canonical(token: str) -> bytes:
    return "".join(c for c in token.upper() if c.isalnum()).encode("utf-8", "replace")
