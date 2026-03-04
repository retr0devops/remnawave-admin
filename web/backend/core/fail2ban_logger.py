"""Fail2ban-compatible auth failure logger.

Writes structured log lines to a dedicated file that fail2ban can parse.
Format: FAIL2BAN: auth_failure ip=<IP> user=<USER> method=<METHOD> reason=<REASON>
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    _logger = logging.getLogger("fail2ban.auth")
    _logger.setLevel(logging.WARNING)
    _logger.propagate = False

    log_dir = Path("/app/logs")
    if not log_dir.exists():
        log_dir.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        log_dir / "auth_failures.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _logger.addHandler(handler)
    return _logger


def log_auth_failure(ip: str, username: str, method: str, reason: str = "") -> None:
    """Log a failed authentication attempt for fail2ban to pick up."""
    try:
        from shared.config_service import config_service
        if not config_service.get("auth_fail2ban_logging", True):
            return
    except Exception:
        pass  # If config_service unavailable, log by default
    safe_user = username.replace(" ", "_")[:100]
    safe_reason = reason.replace("\n", " ")[:200]
    _get_logger().warning(
        "FAIL2BAN: auth_failure ip=%s user=%s method=%s reason=%s",
        ip, safe_user, method, safe_reason,
    )
