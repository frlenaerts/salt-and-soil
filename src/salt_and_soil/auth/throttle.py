from __future__ import annotations

import time
from threading import Lock


class LoginThrottle:
    """In-memory brute-force protection for login attempts.

    After MAX_FAILURES consecutive failures, locks out further attempts for
    LOCKOUT_SECONDS. A successful login resets the counter.

    State is per-process — restarting the server clears the lockout.
    """

    def __init__(self, max_failures: int = 5, lockout_seconds: int = 15 * 60):
        self.max_failures    = max_failures
        self.lockout_seconds = lockout_seconds
        self._lock           = Lock()
        self._failures       = 0
        self._locked_until   = 0.0  # monotonic timestamp

    def seconds_remaining(self) -> float:
        """Return seconds left on the current lockout (0 if not locked)."""
        with self._lock:
            now = time.monotonic()
            return max(0.0, self._locked_until - now)

    def record_failure(self) -> float:
        """Register a failed attempt. Returns seconds remaining on lockout
        (0 if not locked yet)."""
        with self._lock:
            now = time.monotonic()
            if self._locked_until > now:
                return self._locked_until - now
            self._failures += 1
            if self._failures >= self.max_failures:
                self._locked_until = now + self.lockout_seconds
                self._failures    = 0
                return float(self.lockout_seconds)
            return 0.0

    def record_success(self) -> None:
        with self._lock:
            self._failures     = 0
            self._locked_until = 0.0
