from __future__ import annotations

import ipaddress
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock


def client_key(host: str | None) -> str:
    """Throttle key for a remote address: the address itself for IPv4, the
    /64 network for IPv6 (one machine can use any address in its /64, so
    counting single v6 addresses would be trivial to sidestep)."""
    if not host:
        return "unknown"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return f"{ipaddress.ip_network((ip, 64), strict=False).network_address}/64"
    return str(ip)


@dataclass
class _Client:
    failures:     int   = 0
    locked_until: float = 0.0   # monotonic timestamp
    last_seen:    float = 0.0


class LoginThrottle:
    """In-memory brute-force protection for login attempts.

    Per client: after `max_failures` consecutive failures, that client is
    locked out for `lockout_seconds`. Other clients are not affected, so a
    device that keeps guessing cannot lock the operator out. A successful
    login resets the client's counter.

    Global ceiling: `global_max_failures` failures from all clients together
    within `lockout_seconds` lock everyone out for `lockout_seconds`. This
    bounds an attacker who spreads guesses over many addresses, at the cost
    that such an attacker can still cause a lockout.

    State is per-process — restarting the server clears it.
    """

    def __init__(
        self,
        max_failures: int = 5,
        lockout_seconds: int = 15 * 60,
        global_max_failures: int = 20,
        max_clients: int = 1024,
    ):
        self.max_failures        = max_failures
        self.lockout_seconds     = lockout_seconds
        self.global_max_failures = global_max_failures
        self.max_clients         = max_clients
        self._lock               = Lock()
        self._clients: dict[str, _Client] = {}
        self._recent: deque[float] = deque()    # failure times, all clients
        self._global_locked_until = 0.0

    def seconds_remaining(self, client: str = "") -> float:
        """Seconds left on the lockout that applies to `client` (0 if none)."""
        with self._lock:
            return self._remaining(client, time.monotonic())

    def record_failure(self, client: str = "") -> float:
        """Register a failed attempt. Returns seconds remaining on the
        resulting lockout (0 if not locked yet)."""
        with self._lock:
            now = time.monotonic()
            remaining = self._remaining(client, now)
            if remaining > 0:
                return remaining

            entry = self._clients.get(client)
            if entry is None:
                self._make_room(now)
                entry = self._clients[client] = _Client()
            entry.last_seen = now
            entry.failures += 1
            locked = False
            if entry.failures >= self.max_failures:
                entry.locked_until = now + self.lockout_seconds
                entry.failures     = 0
                locked = True

            self._recent.append(now)
            while self._recent and self._recent[0] <= now - self.lockout_seconds:
                self._recent.popleft()
            if len(self._recent) >= self.global_max_failures:
                self._global_locked_until = now + self.lockout_seconds
                self._recent.clear()
                locked = True

            # Exact value rather than (now + lockout) - now, which float
            # rounding can push a hair over and turn "15 minutes" into 16.
            return float(self.lockout_seconds) if locked else 0.0

    def record_success(self, client: str = "") -> None:
        with self._lock:
            self._clients.pop(client, None)

    # ── internals (call with the lock held) ───────────────────────────────────

    def _remaining(self, client: str, now: float) -> float:
        entry = self._clients.get(client)
        until = max(self._global_locked_until, entry.locked_until if entry else 0.0)
        return float(max(0.0, until - now))

    def _make_room(self, now: float) -> None:
        """Keep the table bounded: an attacker must not be able to grow it
        without limit by connecting from many addresses."""
        if len(self._clients) < self.max_clients:
            return
        stale = now - self.lockout_seconds
        for key in [k for k, c in self._clients.items()
                    if c.locked_until <= now and c.last_seen <= stale]:
            del self._clients[key]
        while len(self._clients) >= self.max_clients:
            oldest = min(self._clients, key=lambda k: self._clients[k].last_seen)
            del self._clients[oldest]
