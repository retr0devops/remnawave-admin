"""Brute-force protection for login endpoints.

Tracks failed login attempts per IP and locks out after threshold.
Settings are read from config_service at runtime (auth_max_attempts, auth_lockout_minutes).
"""
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Dict

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL = 300     # Clean stale entries every 5 minutes


def _get_max_attempts() -> int:
    try:
        from shared.config_service import config_service
        return config_service.get("auth_max_attempts", 5)
    except Exception:
        return 5


def _get_lockout_seconds() -> int:
    try:
        from shared.config_service import config_service
        return config_service.get("auth_lockout_minutes", 15) * 60
    except Exception:
        return 900


@dataclass
class _IPRecord:
    failures: int = 0
    locked_until: float = 0.0
    last_attempt: float = field(default_factory=time.time)


class LoginGuard:
    """In-memory per-IP brute-force protection."""

    def __init__(self):
        self._records: Dict[str, _IPRecord] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.time()

    def is_locked(self, ip: str) -> bool:
        """Check if IP is currently locked out."""
        with self._lock:
            rec = self._records.get(ip)
            if not rec:
                return False
            if rec.locked_until > time.time():
                return True
            # Lockout expired — reset
            if rec.locked_until > 0:
                rec.failures = 0
                rec.locked_until = 0.0
            return False

    def remaining_seconds(self, ip: str) -> int:
        """Get remaining lockout seconds for an IP (0 if not locked)."""
        with self._lock:
            rec = self._records.get(ip)
            if not rec:
                return 0
            remaining = rec.locked_until - time.time()
            return max(0, int(remaining))

    def record_failure(self, ip: str) -> bool:
        """Record a failed login attempt. Returns True if IP is now locked."""
        with self._lock:
            self._maybe_cleanup()
            rec = self._records.get(ip)
            if not rec:
                rec = _IPRecord()
                self._records[ip] = rec

            rec.failures += 1
            rec.last_attempt = time.time()

            max_attempts = _get_max_attempts()
            lockout_seconds = _get_lockout_seconds()
            if rec.failures >= max_attempts:
                rec.locked_until = time.time() + lockout_seconds
                logger.warning(
                    "IP %s locked out for %ds after %d failed login attempts",
                    ip, lockout_seconds, rec.failures,
                )
                return True
            return False

    def record_success(self, ip: str) -> None:
        """Reset failure counter on successful login."""
        with self._lock:
            self._records.pop(ip, None)

    def _maybe_cleanup(self) -> None:
        """Remove stale entries (called under lock)."""
        now = time.time()
        if now - self._last_cleanup < CLEANUP_INTERVAL:
            return
        self._last_cleanup = now
        lockout_seconds = _get_lockout_seconds()
        stale = [
            ip for ip, rec in self._records.items()
            if rec.locked_until < now and now - rec.last_attempt > lockout_seconds
        ]
        for ip in stale:
            del self._records[ip]


login_guard = LoginGuard()
