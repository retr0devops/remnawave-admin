"""
Collector API для приёма данных о подключениях от Node Agent.

Endpoint: POST /batch
Аутентификация: Bearer token (токен агента из таблицы nodes.agent_token)

Заменяет аналогичный endpoint из бота (src/services/collector.py),
перенося всю логику violation detection в web backend.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from shared.banhammer import BanhammerService
from shared.bedolaga_client import bedolaga_client
from shared.connection_types import normalize_connection_type
from shared.database import db_service
from shared.connection_monitor import ConnectionMonitor
from shared.violation_detector import IntelligentViolationDetector, ViolationAction
from shared.agent_tokens import get_node_by_token
from shared.config_service import config_service
from web.backend.core.config import get_web_settings

logger = logging.getLogger(__name__)

# Инициализируем сервисы (синглтоны на уровне модуля)
connection_monitor = ConnectionMonitor(db_service)
violation_detector = IntelligentViolationDetector(db_service, connection_monitor)
banhammer_service = BanhammerService(db_service)

# Per-user cooldown for violation checks (avoid re-checking every 30s batch)
_violation_check_cooldown: dict[str, datetime] = {}
_cooldown_lock = asyncio.Lock()
VIOLATION_CHECK_COOLDOWN_MINUTES = 15
MAX_COOLDOWN_SIZE = 100000

# Periodic cleanup of old violations
_last_violation_cleanup: datetime = datetime.min

# Periodic cleanup of old metrics snapshots and connections
_last_metrics_cleanup: datetime = datetime.min
CLEANUP_INTERVAL_HOURS = 24
METRICS_RETENTION_DAYS = 30
CONNECTIONS_RETENTION_DAYS = 30

# Semaphore: limit concurrent background violation detection batches
_violation_semaphore = asyncio.Semaphore(3)

# ── Violation detection queue ──────────────────────────────
# Instead of spawning a task per batch, accumulate user UUIDs in a set
# and drain them in a single background worker. No data is ever dropped.
_pending_violation_users: set = set()
_violation_worker_task: Optional[asyncio.Task] = None
_VIOLATION_DRAIN_INTERVAL = 3.0  # seconds between drain cycles
_VIOLATION_CHUNK_SIZE = 200      # max users per drain cycle

# ── Queue metrics ─────────────────────────────────────────
_stats = {
    "total_enqueued": 0,         # Total users ever enqueued
    "total_processed": 0,        # Total users processed by worker
    "total_violations_found": 0, # Total violations detected
    "total_skipped_cooldown": 0, # Skipped due to cooldown
    "total_batches_received": 0, # Total HTTP batch requests
    "total_batches_rejected": 0, # Rate-limited batch requests
    "total_tasks_dropped": 0,    # Background tasks dropped (torrent etc.)
    "peak_queue_size": 0,        # Peak queue size seen
    "last_drain_duration_ms": 0, # Last drain cycle duration
    "worker_started_at": None,   # When worker was last started
}

async def _violation_worker():
    """Single long-lived worker that drains _pending_violation_users in chunks."""
    import time
    _stats["worker_started_at"] = datetime.utcnow().isoformat()

    while True:
        try:
            # Read configurable parameters each cycle (hot-reload from settings)
            drain_interval = config_service.get("violation_drain_interval", _VIOLATION_DRAIN_INTERVAL)
            chunk_size = config_service.get("violation_chunk_size", _VIOLATION_CHUNK_SIZE)
            await asyncio.sleep(drain_interval)
            if not _pending_violation_users:
                continue

            # Track peak queue size
            queue_size = len(_pending_violation_users)
            if queue_size > _stats["peak_queue_size"]:
                _stats["peak_queue_size"] = queue_size

            # Take only a chunk, leave the rest for next cycle
            batch = set()
            while _pending_violation_users and len(batch) < chunk_size:
                batch.add(_pending_violation_users.pop())

            remaining = len(_pending_violation_users)
            if remaining > 0:
                logger.info("Violation queue: processing %d, %d remaining", len(batch), remaining)

            t0 = time.monotonic()
            await _run_violation_detection(batch)
            _stats["last_drain_duration_ms"] = int((time.monotonic() - t0) * 1000)
            _stats["total_processed"] += len(batch)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Violation worker error: %s", e, exc_info=True)

def _enqueue_violation_users(user_uuids: set):
    """Add users to the pending violation check queue and ensure the worker is running."""
    global _violation_worker_task
    _pending_violation_users.update(user_uuids)
    _stats["total_enqueued"] += len(user_uuids)

    # Start worker if not running
    if _violation_worker_task is None or _violation_worker_task.done():
        _violation_worker_task = asyncio.create_task(_violation_worker())

# ── Generic background task helper (for torrent etc.) ──────
_background_tasks: set = set()
_MAX_BACKGROUND_TASKS = 20

def _schedule_background_task(coro):
    """Schedule a background task with tracking and bounded concurrency."""
    done = {t for t in _background_tasks if t.done()}
    _background_tasks.difference_update(done)

    max_tasks = config_service.get("violation_max_background_tasks", _MAX_BACKGROUND_TASKS)
    if len(_background_tasks) >= max_tasks:
        logger.warning(
            "Background task dropped: %d tasks already queued (limit %d)",
            len(_background_tasks), max_tasks,
        )
        _stats["total_tasks_dropped"] += 1
        return

    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# Кэш имён нод: {node_uuid: (node_name, cached_at)}
_node_name_cache: dict[str, tuple[str, datetime]] = {}
_NODE_NAME_TTL_MINUTES = 30
_MOBILE_CONNECTION_TYPES = {"mobile", "mobile_isp"}

# Rate limiter для /batch: не более одного запроса в секунду с одной ноды
_node_last_batch: dict[str, float] = {}
MIN_BATCH_INTERVAL = 1.0  # seconds


async def _get_node_name(node_uuid: str) -> str:
    """Вернуть имя ноды по UUID (с кэшем и TTL). Fallback — первые 8 символов UUID."""
    cached = _node_name_cache.get(node_uuid)
    now = datetime.now()
    if cached and (now - cached[1]).total_seconds() < _NODE_NAME_TTL_MINUTES * 60:
        return cached[0]
    try:
        node = await db_service.get_node_by_uuid(node_uuid)
        name = node.get("name") or node_uuid[:8] if node else node_uuid[:8]
        _node_name_cache[node_uuid] = (name, now)
        return name
    except Exception:
        return cached[0] if cached else node_uuid[:8]


def _normalize_banhammer_types(raw_values) -> set[str]:
    """Normalize connection_type values from Banhammer mismatch payload."""
    normalized: set[str] = set()
    if not isinstance(raw_values, list):
        return normalized
    for raw_value in raw_values:
        value = normalize_connection_type(raw_value)
        if value:
            normalized.add(value)
    return normalized


def _resolve_network_notification_type(mismatches: list[dict]) -> tuple[str, Optional[str]]:
    """
    Map Banhammer mismatch details to Bedolaga notification type.

    Returns:
        (notification_type, network_type_hint)
    """
    actual_types: set[str] = set()
    expected_types: set[str] = set()

    for mismatch in mismatches or []:
        if not isinstance(mismatch, dict):
            continue
        actual_types.update(_normalize_banhammer_types(mismatch.get("actual_connection_types")))
        expected_types.update(_normalize_banhammer_types(mismatch.get("expected_connection_types")))

    if actual_types.intersection(_MOBILE_CONNECTION_TYPES):
        notification_type = "network_mobile"
    elif expected_types and expected_types.issubset(_MOBILE_CONNECTION_TYPES):
        notification_type = "network_wifi"
    elif expected_types and expected_types.isdisjoint(_MOBILE_CONNECTION_TYPES):
        notification_type = "network_mobile"
    else:
        notification_type = "network_wifi"

    network_type_hint = ", ".join(sorted(actual_types)) if actual_types else None
    return notification_type, network_type_hint


def _build_warning_message(result) -> str:
    """Build warning message text for Bedolaga notification endpoint."""
    base_message = str(getattr(result, "message", "") or "").strip()
    if not base_message:
        base_message = "Node policy mismatch detected. Reconnect using an allowed network type."

    details: list[str] = []
    for mismatch in (getattr(result, "mismatches", None) or [])[:2]:
        if not isinstance(mismatch, dict):
            continue
        node_uuid = str(mismatch.get("node_uuid") or "").strip()
        actual = _normalize_banhammer_types(mismatch.get("actual_connection_types"))
        expected = _normalize_banhammer_types(mismatch.get("expected_connection_types"))
        if not node_uuid and not actual and not expected:
            continue
        details.append(
            f"node={node_uuid[:8] or 'n/a'} expected={','.join(sorted(expected)) or '-'} "
            f"actual={','.join(sorted(actual)) or '-'}"
        )

    if details:
        base_message = f"{base_message} ({'; '.join(details)})"

    support_contact = config_service.get("banhammer_support_contact", None)
    if support_contact is None:
        support_contact = config_service.get("support_contact", None)
    support_contact_text = str(support_contact).strip() if support_contact is not None else ""
    if support_contact_text:
        return f"{base_message}\nSupport: {support_contact_text}"

    return base_message


def _build_block_message(result) -> Optional[str]:
    """Build block message text for Bedolaga notification endpoint (optional)."""
    message = str(getattr(result, "message", "") or "").strip()
    if not message:
        return None

    support_contact = config_service.get("banhammer_support_contact", None)
    support_contact_text = str(support_contact).strip() if support_contact is not None else ""
    if support_contact_text and support_contact_text not in message:
        return f"{message}\nSupport: {support_contact_text}"
    return message


async def _send_banhammer_user_notification(user_uuid: str, result) -> None:
    """Send user-facing Banhammer warning/block notification via Bedolaga API."""
    if getattr(result, "action", "") not in {"warn", "block"}:
        return

    settings = get_web_settings()
    if not settings.bedolaga_api_url or not settings.bedolaga_api_token:
        return

    user_info = await db_service.get_user_by_uuid(user_uuid) or {}
    username_raw = str(user_info.get("username") or "").strip()
    email_raw = str(user_info.get("email") or "").strip()

    # Bedolaga resolves user by identifier through Remnawave:
    # it first tries username lookup, then falls back to email.
    user_identifier = username_raw or email_raw
    username = username_raw or user_uuid[:8]
    if not user_identifier:
        logger.debug(
            "Skipping Bedolaga Banhammer notification for %s: username/email are missing",
            user_uuid,
        )
        return

    if not bedolaga_client.is_configured:
        bedolaga_client.configure(settings.bedolaga_api_url, settings.bedolaga_api_token)

    mismatches = list(getattr(result, "mismatches", None) or [])
    node_name = None
    if mismatches:
        first_match = mismatches[0] if isinstance(mismatches[0], dict) else {}
        node_uuid = str(first_match.get("node_uuid") or "").strip()
        if node_uuid:
            node_name = await _get_node_name(node_uuid)

    if getattr(result, "action", "") == "warn":
        payload = {
            "notification_type": "warning",
            "user_identifier": user_identifier,
            "username": username,
            "warning_message": _build_warning_message(result),
        }
        if node_name:
            payload["node_name"] = node_name
    else:
        notification_type, network_type_hint = _resolve_network_notification_type(mismatches)
        block_minutes = int(getattr(result, "block_minutes", 0) or 0)
        if block_minutes <= 0:
            block_minutes = 15

        payload = {
            "notification_type": notification_type,
            "user_identifier": user_identifier,
            "username": username,
            "ban_minutes": block_minutes,
        }
        block_message = _build_block_message(result)
        if block_message:
            # Some Bedolaga versions support custom warning_message for block notifications.
            # If the endpoint has strict schema and rejects this field, we'll retry without it.
            payload["warning_message"] = block_message
        if network_type_hint:
            payload["network_type"] = network_type_hint
        if node_name:
            payload["node_name"] = node_name

    try:
        await bedolaga_client.send_ban_notification(payload)
        logger.info(
            "Banhammer user notification sent via Bedolaga: user=%s action=%s type=%s",
            user_uuid,
            getattr(result, "action", ""),
            payload.get("notification_type"),
        )
    except Exception as e:
        if (
            getattr(result, "action", "") == "block"
            and isinstance(payload, dict)
            and "warning_message" in payload
        ):
            retry_payload = dict(payload)
            retry_payload.pop("warning_message", None)
            try:
                await bedolaga_client.send_ban_notification(retry_payload)
                logger.info(
                    "Banhammer block notification sent via Bedolaga without custom warning_message: user=%s",
                    user_uuid,
                )
                return
            except Exception as retry_error:
                logger.warning(
                    "Bedolaga Banhammer block notification retry failed: user=%s error=%s",
                    user_uuid,
                    retry_error,
                )

        logger.warning(
            "Bedolaga Banhammer notification failed: user=%s action=%s error=%s",
            user_uuid,
            getattr(result, "action", ""),
            e,
        )

router = APIRouter()


# ── Pydantic models ──────────────────────────────────────────────


class ConnectionReport(BaseModel):
    """Одно подключение от агента."""
    user_email: str
    ip_address: str
    node_uuid: str
    connected_at: datetime
    disconnected_at: Optional[datetime] = None
    bytes_sent: int = 0
    bytes_received: int = 0


class SystemMetricsReport(BaseModel):
    """Системные метрики ноды."""
    cpu_percent: float = 0.0
    cpu_cores: int = 0
    memory_percent: float = 0.0
    memory_total_bytes: int = 0
    memory_used_bytes: int = 0
    disk_percent: float = 0.0
    disk_total_bytes: int = 0
    disk_used_bytes: int = 0
    disk_read_speed_bps: int = 0
    disk_write_speed_bps: int = 0
    uptime_seconds: int = 0


class TorrentEventReport(BaseModel):
    """Торрент-событие от агента."""
    user_email: str
    ip_address: str
    destination: str
    inbound_tag: str = ""
    outbound_tag: str = "TORRENT"
    node_uuid: str
    detected_at: datetime


class BatchReport(BaseModel):
    """Батч подключений от одной ноды."""
    node_uuid: str
    timestamp: datetime
    connections: list[ConnectionReport] = Field(default=[], max_length=5000)
    torrent_events: list[TorrentEventReport] = Field(default=[], max_length=1000)
    system_metrics: Optional[SystemMetricsReport] = None


# ── Auth ─────────────────────────────────────────────────────────


async def _find_user_uuid_by_identifier(identifier: str) -> Optional[str]:
    """Поиск user_uuid по email, short_uuid или raw_data ID."""
    user_uuid = None

    if identifier.startswith("user_"):
        user_id_str = identifier.replace("user_", "")
        user = await db_service.get_user_by_short_uuid(user_id_str)
        if user:
            user_uuid = user.get("uuid")

    if not user_uuid:
        user_uuid = await db_service.get_user_uuid_by_email(identifier)

    if not user_uuid and identifier.startswith("user_"):
        user_id_str = identifier.replace("user_", "")
        user_uuid = await db_service.get_user_uuid_by_id_from_raw_data(user_id_str)

    return user_uuid


async def verify_agent_token(
    request: Request,
    authorization: str = Header(..., alias="Authorization"),
) -> str:
    """Проверяет Bearer token агента. Возвращает node_uuid."""
    client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )

    logger.debug("Verifying agent token (length: %d) from %s", len(authorization) if authorization else 0, client_ip)

    if not authorization.startswith("Bearer "):
        logger.warning("Invalid authorization header format from %s", client_ip)
        raise HTTPException(status_code=401, detail="Invalid authorization header format")

    token = authorization[7:].strip()
    if not token:
        logger.warning("Token is empty, from %s", client_ip)
        raise HTTPException(status_code=401, detail="Token is required")

    node_uuid = await get_node_by_token(db_service, token)
    if not node_uuid:
        node_name_hint = ""
        try:
            async with db_service.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT name, address FROM nodes WHERE address LIKE $1 LIMIT 1",
                    f"%{client_ip}%",
                )
                if row:
                    node_name_hint = f" (possible node: {row['name']} / {row['address']})"
        except Exception as e:
            logger.debug("Failed to resolve node name by IP: %s", e)
        logger.warning(
            "Invalid agent token attempted: %s from %s%s",
            token[:8] + "...", client_ip, node_name_hint,
        )
        raise HTTPException(status_code=403, detail="Invalid or expired token")

    logger.debug("Agent token verified for node: %s from %s", node_uuid, client_ip)
    return node_uuid


# ── Endpoints ────────────────────────────────────────────────────


@router.post("/batch")
async def receive_connections(
    report: BatchReport,
    request: Request,
    node_uuid: str = Depends(verify_agent_token),
):
    """Принимает батч подключений от Node Agent."""
    # Rate limit: reject if less than MIN_BATCH_INTERVAL seconds since last batch
    now_ts = time.monotonic()
    last_ts = _node_last_batch.get(node_uuid, 0.0)
    if now_ts - last_ts < MIN_BATCH_INTERVAL:
        _stats["total_batches_rejected"] += 1
        raise HTTPException(status_code=429, detail="Too many requests: batch interval too short")
    _node_last_batch[node_uuid] = now_ts
    _stats["total_batches_received"] += 1

    node_name = await _get_node_name(node_uuid)
    logger.info(
        "Batch received: node=%s connections=%d metrics=%s",
        node_name, len(report.connections) if report.connections else 0,
        "yes" if report.system_metrics else "no",
    )

    if report.node_uuid != node_uuid:
        logger.warning("Node UUID mismatch: token=%s, report=%s", node_uuid, report.node_uuid)
        raise HTTPException(status_code=403, detail="Token does not match the reported node UUID")

    # System metrics
    if report.system_metrics:
        try:
            await db_service.update_node_metrics(
                node_uuid=node_uuid,
                cpu_usage=report.system_metrics.cpu_percent,
                cpu_cores=report.system_metrics.cpu_cores,
                memory_usage=report.system_metrics.memory_percent,
                memory_total_bytes=report.system_metrics.memory_total_bytes,
                memory_used_bytes=report.system_metrics.memory_used_bytes,
                disk_usage=report.system_metrics.disk_percent,
                disk_total_bytes=report.system_metrics.disk_total_bytes,
                disk_used_bytes=report.system_metrics.disk_used_bytes,
                disk_read_speed_bps=report.system_metrics.disk_read_speed_bps,
                disk_write_speed_bps=report.system_metrics.disk_write_speed_bps,
                uptime_seconds=report.system_metrics.uptime_seconds,
            )
            logger.debug("System metrics updated for node %s", node_uuid)

            # Save snapshot for historical analytics
            try:
                await db_service.insert_node_metrics_snapshot(
                    node_uuid=node_uuid,
                    cpu_usage=report.system_metrics.cpu_percent,
                    cpu_cores=report.system_metrics.cpu_cores,
                    memory_usage=report.system_metrics.memory_percent,
                    memory_total_bytes=report.system_metrics.memory_total_bytes,
                    memory_used_bytes=report.system_metrics.memory_used_bytes,
                    disk_usage=report.system_metrics.disk_percent,
                    disk_total_bytes=report.system_metrics.disk_total_bytes,
                    disk_used_bytes=report.system_metrics.disk_used_bytes,
                    disk_read_speed_bps=report.system_metrics.disk_read_speed_bps,
                    disk_write_speed_bps=report.system_metrics.disk_write_speed_bps,
                    uptime_seconds=report.system_metrics.uptime_seconds,
                )
            except Exception as e:
                logger.debug("Failed to save metrics snapshot for node %s: %s", node_uuid, e)
        except Exception as e:
            logger.warning("Failed to update system metrics for node %s: %s", node_uuid, e)

    # Periodic cleanup of old data (once per 24h)
    global _last_metrics_cleanup
    now = datetime.utcnow()
    if (now - _last_metrics_cleanup).total_seconds() > CLEANUP_INTERVAL_HOURS * 3600:
        _last_metrics_cleanup = now
        try:
            deleted = await db_service.cleanup_old_metrics_snapshots(METRICS_RETENTION_DAYS)
            if deleted > 0:
                logger.info("Cleaned up %d old metrics snapshots", deleted)
        except Exception as e:
            logger.warning("Failed to cleanup old metrics snapshots: %s", e)
        try:
            deleted = await db_service.cleanup_old_connections(CONNECTIONS_RETENTION_DAYS)
            if deleted > 0:
                logger.info("Cleaned up %d old connections", deleted)
        except Exception as e:
            logger.warning("Failed to cleanup old connections: %s", e)
        try:
            deleted = await db_service.cleanup_old_torrent_events(90)
            if deleted > 0:
                logger.info("Cleaned up %d old torrent events", deleted)
        except Exception as e:
            logger.warning("Failed to cleanup old torrent events: %s", e)

    if not report.connections and not report.torrent_events:
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "processed": 0, "message": "No connections to process",
                     "metrics_updated": report.system_metrics is not None},
        )

    # ── Batch resolve all user identifiers to UUIDs ──────────────
    user_uuid_cache: dict[str, Optional[str]] = {}

    if report.connections or report.torrent_events:
        all_identifiers = set()
        for conn in report.connections:
            all_identifiers.add(conn.user_email)
        for event in (report.torrent_events or []):
            all_identifiers.add(event.user_email)

        # Classify identifiers
        emails = []
        short_uuids_raw = []
        for ident in all_identifiers:
            if ident.startswith("user_"):
                short_uuids_raw.append(ident)
            else:
                emails.append(ident)

        # Batch resolve emails and short_uuids (2 queries instead of N)
        if emails:
            email_map = await db_service.get_email_to_uuid_map(emails)
            for email, uid in email_map.items():
                user_uuid_cache[email] = uid

        if short_uuids_raw:
            short_uuids_clean = [s.replace("user_", "") for s in short_uuids_raw]
            short_map = await db_service.get_short_uuid_to_uuid_map(short_uuids_clean)
            for short, uid in short_map.items():
                user_uuid_cache[f"user_{short}"] = uid

        # Fallback for unresolved identifiers (individual lookup)
        for ident in all_identifiers:
            if ident not in user_uuid_cache:
                uid = await _find_user_uuid_by_identifier(ident)
                user_uuid_cache[ident] = uid

    # Helper for torrent events (reuses the cache)
    async def _cached_find_user(identifier: str) -> Optional[str]:
        if identifier not in user_uuid_cache:
            user_uuid_cache[identifier] = await _find_user_uuid_by_identifier(identifier)
        return user_uuid_cache[identifier]

    # ── Batch upsert connections (replaces per-connection loop) ─────
    processed = 0
    errors = 0

    if report.connections:
        batch_connections = []
        for conn in report.connections:
            user_uuid = user_uuid_cache.get(conn.user_email)
            if not user_uuid:
                logger.warning("User not found for identifier=%s, skipping", conn.user_email)
                errors += 1
                continue
            batch_connections.append({
                "user_uuid": user_uuid,
                "ip_address": conn.ip_address,
                "node_uuid": conn.node_uuid,
                "device_info": {
                    "user_email": conn.user_email,
                    "bytes_sent": conn.bytes_sent,
                    "bytes_received": conn.bytes_received,
                    "connected_at": conn.connected_at.isoformat() if conn.connected_at else None,
                    "disconnected_at": conn.disconnected_at.isoformat() if conn.disconnected_at else None,
                },
                "connected_at": conn.connected_at,
            })

        if batch_connections:
            try:
                result = await db_service.batch_upsert_connections(
                    batch_connections, stale_threshold_minutes=2
                )
                processed = result["upserted"]
                logger.info(
                    "Batch upserted: node=%s upserted=%d closed_stale=%d errors=%d",
                    node_name, result["upserted"], result["closed_stale"], errors,
                )
            except Exception as e:
                logger.error("Batch upsert failed for node %s: %s", node_name, e, exc_info=True)
                errors += len(batch_connections)

    if errors > 0:
        logger.warning("Batch processed with errors: node=%s total=%d processed=%d errors=%d",
                       node_uuid, len(report.connections), processed, errors)

    # Post-processing: violation detection in background
    # Stale connection closing is now handled inside batch_upsert_connections
    if processed > 0:
        try:
            # Only include users that had connections in this batch (not torrent-only users)
            affected_user_uuids = set(
                user_uuid_cache[conn.user_email]
                for conn in report.connections
                if user_uuid_cache.get(conn.user_email)
            )
            _enqueue_violation_users(affected_user_uuids)
        except Exception as e:
            logger.warning("Error in post-processing: %s", e)

    # ── Torrent events processing ──────────────────────────
    torrent_processed = 0
    if report.torrent_events:
        torrent_enabled = config_service.get("torrent_detection_enabled", True)
        if torrent_enabled:
            # Resolve user UUIDs and build batch
            batch_events = []
            for event in report.torrent_events:
                try:
                    user_uuid_t = await _cached_find_user(event.user_email)
                    if not user_uuid_t:
                        continue
                    batch_events.append({
                        "user_uuid": user_uuid_t,
                        "node_uuid": event.node_uuid,
                        "ip_address": event.ip_address,
                        "destination": event.destination,
                        "inbound_tag": event.inbound_tag,
                        "outbound_tag": event.outbound_tag,
                        "detected_at": event.detected_at,
                    })
                except Exception as e:
                    logger.warning("Error resolving torrent event for %s: %s", event.user_email, e)

            if batch_events:
                torrent_processed = await db_service.batch_save_torrent_events(batch_events)

            if torrent_processed > 0:
                logger.warning(
                    "Torrent events: node=%s count=%d", node_name, torrent_processed
                )
                _schedule_background_task(
                    _process_torrent_violations(report.torrent_events, user_uuid_cache)
                )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok", "processed": processed, "errors": errors,
            "torrent_events": torrent_processed, "node_uuid": node_uuid,
        },
    )


async def _process_torrent_violations(
    events: list[TorrentEventReport],
    user_uuid_cache: dict[str, Optional[str]],
):
    """Background: create violations and send notifications for torrent events."""
    try:
        # Group events by user
        events_by_user: dict[str, list[TorrentEventReport]] = {}
        for event in events:
            user_uuid = user_uuid_cache.get(event.user_email)
            if user_uuid:
                events_by_user.setdefault(user_uuid, []).append(event)

        auto_action = config_service.get("torrent_auto_action", "notify")

        for user_uuid, user_events in events_by_user.items():
            try:
                # Dedup: skip if torrent violation exists within last 10 min
                existing = await db_service.get_recent_torrent_violation(user_uuid, minutes=10)
                if existing:
                    continue

                user_info = await db_service.get_user_by_uuid(user_uuid)
                username = user_info.get("username", "n/a") if user_info else "n/a"
                email = user_info.get("email") if user_info else None
                telegram_id = user_info.get("telegramId") if user_info else None

                destinations = list(set(e.destination for e in user_events))
                ips = list(set(e.ip_address for e in user_events))

                # Save as violation (score=100)
                violation_id = await db_service.save_violation(
                    user_uuid=user_uuid,
                    score=100.0,
                    recommended_action="hard_block",
                    username=username,
                    email=email,
                    telegram_id=telegram_id,
                    confidence=1.0,
                    ip_addresses=ips,
                    reasons=[
                        f"Torrent traffic detected ({len(user_events)} events)",
                        *[f"Destination: {d}" for d in destinations[:5]],
                    ],
                    simultaneous_connections=len(ips),
                    unique_ips_count=len(ips),
                )

                # Notification
                try:
                    from web.backend.core.violation_notifier import send_torrent_notification
                    await send_torrent_notification(
                        user_uuid=user_uuid,
                        user_info=user_info,
                        torrent_events=user_events,
                        destinations=destinations,
                        ips=ips,
                    )
                except Exception as e:
                    logger.warning("Failed to send torrent notification: %s", e)

                # Automation event
                try:
                    from web.backend.core.automation_engine import engine as automation_engine
                    await automation_engine.handle_event("torrent.detected", {
                        "user_uuid": user_uuid,
                        "uuid": user_uuid,
                        "username": username,
                        "email": email,
                        "destinations": destinations,
                        "ips": ips,
                        "event_count": len(user_events),
                        "node_uuid": user_events[0].node_uuid,
                        "score": 100.0,
                    })
                except Exception as e:
                    logger.warning("Automation event failed: %s", e)

                # WebSocket broadcast
                try:
                    from web.backend.api.v2.websocket import broadcast_violation
                    await broadcast_violation({
                        "type": "torrent",
                        "user_uuid": user_uuid,
                        "username": username,
                        "score": 100.0,
                        "destinations": destinations,
                        "reasons": [f"Torrent traffic: {len(user_events)} events"],
                    })
                except Exception as e:
                    logger.debug("WebSocket broadcast failed for torrent violation: %s", e)

                # Auto-block if configured
                if auto_action == "block_user":
                    try:
                        from shared.api_client import api_client
                        await api_client.disable_user(user_uuid)
                        logger.info("Auto-blocked user %s for torrent usage", user_uuid)
                    except Exception as e:
                        logger.warning("Failed to auto-block user %s: %s", user_uuid, e)

            except Exception as e:
                logger.warning("Error processing torrent violation for user %s: %s", user_uuid, e)

    except Exception as e:
        logger.error("Background torrent violation processing failed: %s", e)


async def _lookup_ip_metadata_for_connections(active_conns):
    """Lookup IP metadata for a set of active connections."""
    if not active_conns:
        return {}

    unique_ips = list({str(c.ip_address) for c in active_conns if getattr(c, "ip_address", None)})
    if not unique_ips:
        return {}

    try:
        from shared.geoip import get_geoip_service

        geoip = get_geoip_service()
        return await geoip.lookup_batch(unique_ips)
    except Exception as e:
        logger.debug("GeoIP lookup failed: %s", e)
        return {}


async def _run_banhammer_for_user(user_uuid: str):
    """
    Run Banhammer checks independently from anti-abuse detector.

    Returns:
        tuple(active_connections, ip_metadata_cache)
    """
    active_conns = []
    ip_metadata = {}

    try:
        banhammer_enabled = config_service.get("banhammer_enabled", False)

        if banhammer_enabled:
            active_conns = await connection_monitor.get_user_active_connections(user_uuid, max_age_minutes=5)
            ip_metadata = await _lookup_ip_metadata_for_connections(active_conns)

        result = await banhammer_service.process_user(
            user_uuid=user_uuid,
            active_connections=active_conns,
            ip_metadata_cache=ip_metadata,
        )

        if result.action in {"warn", "block"}:
            await _send_banhammer_user_notification(user_uuid, result)

        if result.action in {"warn", "block", "blocked", "block_failed", "unblock_failed"}:
            logger.info(
                "Banhammer result: user=%s action=%s warnings=%d stage=%d blocked_until=%s",
                user_uuid,
                result.action,
                result.warning_count,
                result.block_stage,
                result.blocked_until.isoformat() if result.blocked_until else None,
            )
    except Exception as e:
        logger.warning("Banhammer processing failed for %s: %s", user_uuid, e)

    return active_conns, ip_metadata


async def _check_single_user(
    user_uuid: str,
    min_score: float,
    sem: asyncio.Semaphore,
    cooldown_override: Optional[int] = None,
    run_anti_abuse: bool = True,
):
    """Check a single user for violations (with semaphore for concurrency control)."""
    async with sem:
        try:
            banhammer_active_conns, banhammer_ip_metadata = await _run_banhammer_for_user(user_uuid)

            if not run_anti_abuse:
                return

            # Whitelist check
            whitelisted, excluded_analyzers = await db_service.is_user_violation_whitelisted(user_uuid)
            if whitelisted and excluded_analyzers is None:
                logger.debug("User %s is fully whitelisted, skipping violation check", user_uuid)
                return

            # Per-user cooldown (adaptive or config-based)
            now_check = datetime.utcnow()
            last_check = _violation_check_cooldown.get(user_uuid)
            cooldown_minutes = cooldown_override if cooldown_override is not None else config_service.get("violation_check_cooldown_minutes", VIOLATION_CHECK_COOLDOWN_MINUTES)
            if last_check and (now_check - last_check).total_seconds() < cooldown_minutes * 60:
                _stats["total_skipped_cooldown"] += 1
                return

            stats = await connection_monitor.get_user_connection_stats(user_uuid, window_minutes=60)
            if stats:
                logger.debug(
                    "Connection stats for user %s: active=%d, unique_ips=%d, simultaneous=%d",
                    user_uuid, stats.active_connections_count,
                    stats.unique_ips_in_window, stats.simultaneous_connections,
                )

            # Node policy checks are handled by independent Banhammer subsystem.
            # Keep anti-abuse detector isolated from node-policy signals.
            detector_exclusions = set(excluded_analyzers or [])
            detector_exclusions.add("node_policy")
            violation_score = await violation_detector.check_user(
                user_uuid,
                window_minutes=60,
                excluded_analyzers=list(detector_exclusions),
            )

            had_violation = bool(violation_score and violation_score.total >= min_score)

            # ── HWID Blacklist check ──
            try:
                user_devices = await db_service.get_user_hwid_devices(user_uuid)
                if user_devices:
                    user_hwids = [d["hwid"] for d in user_devices if d.get("hwid")]
                    if user_hwids:
                        bl_matches = await db_service.check_hwids_against_blacklist(user_hwids)
                        if bl_matches:
                            from web.backend.api.v2.violations import _handle_blacklisted_hwid_users
                            # Process ALL matched HWIDs (prioritize block over alert)
                            bl_matches.sort(key=lambda m: 0 if m["action"] == "block" else 1)
                            for match in bl_matches:
                                user_entry = [{"user_uuid": user_uuid, "username": None}]
                                await _handle_blacklisted_hwid_users(
                                    match["hwid"],
                                    match["action"],
                                    match.get("reason"),
                                    user_entry,
                                )
                                if match["action"] == "block":
                                    break  # Already blocked, no need to process more
            except Exception as e:
                logger.debug("HWID blacklist check failed for %s: %s", user_uuid, e)

            # Evict oldest 20% entries if cooldown dict is too large
            if len(_violation_check_cooldown) > MAX_COOLDOWN_SIZE:
                sorted_keys = sorted(_violation_check_cooldown, key=_violation_check_cooldown.get)
                evict_count = len(sorted_keys) // 5
                for k in sorted_keys[:evict_count]:
                    _violation_check_cooldown.pop(k, None)
                logger.warning("Cooldown cache eviction: removed %d entries (was %d, limit %d)",
                               evict_count, len(sorted_keys), MAX_COOLDOWN_SIZE)
            # Кулдаун всегда: полный если нарушений нет, короткий (5 мин) если есть
            _violation_check_cooldown[user_uuid] = datetime.utcnow() if not had_violation else (datetime.utcnow() - timedelta(minutes=max(0, cooldown_minutes - 5)))

            if had_violation:
                _stats["total_violations_found"] += 1
                logger.warning(
                    "Violation detected: user=%s score=%.1f action=%s reasons=%s",
                    user_uuid, violation_score.total,
                    violation_score.recommended_action.value,
                    violation_score.reasons[:3],
                )

                active_conns = banhammer_active_conns or await connection_monitor.get_user_active_connections(
                    user_uuid,
                    max_age_minutes=5,
                )
                user_info = await db_service.get_user_by_uuid(user_uuid)

                ip_metadata = banhammer_ip_metadata or await _lookup_ip_metadata_for_connections(active_conns)

                try:
                    from web.backend.core.violation_notifier import send_violation_notification
                    await send_violation_notification(
                        user_uuid=user_uuid,
                        violation_score={
                            "total": violation_score.total,
                            "recommended_action": violation_score.recommended_action,
                            "reasons": violation_score.reasons,
                            "breakdown": violation_score.breakdown,
                            "confidence": violation_score.confidence,
                        },
                        user_info=user_info,
                        active_connections=active_conns,
                        ip_metadata=ip_metadata,
                    )
                except Exception as notify_error:
                    logger.warning("Failed to send violation notification for user %s: %s", user_uuid, notify_error)

                try:
                    breakdown = violation_score.breakdown
                    temporal = breakdown.get("temporal")
                    geo = breakdown.get("geo")
                    asn = breakdown.get("asn")
                    profile = breakdown.get("profile")
                    device = breakdown.get("device")
                    hwid = breakdown.get("hwid")

                    ip_addresses = list(set(str(c.ip_address) for c in active_conns)) if active_conns else None
                    username = user_info.get("username") if user_info else None
                    email = user_info.get("email") if user_info else None
                    telegram_id = user_info.get("telegram_id") if user_info else None
                    device_limit = user_info.get("hwidDeviceLimit", 1) if user_info else 1

                    await db_service.save_violation(
                        user_uuid=user_uuid,
                        score=violation_score.total,
                        recommended_action=violation_score.recommended_action.value,
                        username=username,
                        email=email,
                        telegram_id=telegram_id,
                        confidence=violation_score.confidence,
                        temporal_score=temporal.score if temporal else None,
                        geo_score=geo.score if geo else None,
                        asn_score=asn.score if asn else None,
                        profile_score=profile.score if profile else None,
                        device_score=device.score if device else None,
                        ip_addresses=ip_addresses,
                        countries=list(geo.countries) if geo and geo.countries else None,
                        cities=list(geo.cities) if geo and geo.cities else None,
                        asn_types=list(asn.asn_types) if asn and asn.asn_types else None,
                        os_list=device.os_list if device else None,
                        client_list=device.client_list if device else None,
                        reasons=violation_score.reasons[:10] if violation_score.reasons else None,
                        simultaneous_connections=temporal.simultaneous_connections_count if temporal else None,
                        unique_ips_count=len(ip_addresses) if ip_addresses else None,
                        device_limit=device_limit,
                        impossible_travel=geo.impossible_travel_detected if geo else False,
                        is_mobile=asn.is_mobile_carrier if asn else False,
                        is_datacenter=asn.is_datacenter if asn else False,
                        is_vpn=asn.is_vpn if asn else False,
                        hwid_score=hwid.score if hwid else None,
                        hwid_matched_users=json.dumps(hwid.matched_details) if hwid and hwid.matched_details else None,
                    )
                    logger.info("Violation saved to DB for user %s: score=%.1f", user_uuid, violation_score.total)

                    # Auto-block in Remnawave Panel when hard_block is recommended
                    if violation_score.recommended_action == ViolationAction.HARD_BLOCK:
                        try:
                            from shared.api_client import api_client
                            await api_client.disable_user(user_uuid)
                            logger.info("Auto-blocked user %s via Panel API (hard_block, score=%.1f)", user_uuid, violation_score.total)
                        except Exception as block_error:
                            logger.warning("Failed to auto-block user %s: %s", user_uuid, block_error)

                    # WebSocket broadcast for real-time UI updates
                    try:
                        from web.backend.api.v2.websocket import broadcast_violation
                        await broadcast_violation({
                            "user_uuid": user_uuid,
                            "username": username,
                            "score": violation_score.total,
                            "recommended_action": violation_score.recommended_action.value,
                            "reasons": violation_score.reasons[:5],
                        })
                    except Exception as e:
                        logger.debug("WebSocket broadcast failed for violation: %s", e)

                except Exception as save_error:
                    logger.warning("Failed to save violation to DB for user %s: %s", user_uuid, save_error)
            else:
                if violation_score:
                    logger.info("User %s: score=%.1f (below threshold %.1f)", user_uuid, violation_score.total, min_score)
        except Exception as e:
            logger.warning("Error checking violations for user %s: %s", user_uuid, e)


async def _run_violation_detection(affected_user_uuids: set):
    """Background task: check affected users for violations."""
    try:
        violations_enabled = config_service.get("violations_enabled", True)
        min_score = config_service.get("violations_min_score", 50.0)

        # Cleanup stale cooldown entries (older than 1h)
        now_cleanup = datetime.utcnow()
        expired_keys = [k for k, v in _violation_check_cooldown.items()
                       if (now_cleanup - v).total_seconds() > 3600]
        for k in expired_keys:
            del _violation_check_cooldown[k]
        if expired_keys:
            logger.debug("Cooldown cleanup: removed %d expired entries, %d remaining",
                         len(expired_keys), len(_violation_check_cooldown))

        # Adaptive concurrency and cooldown based on total tracked users
        total_tracked = len(_violation_check_cooldown) + len(affected_user_uuids)
        if total_tracked > 50000:
            max_concurrent = 20
            adaptive_cooldown = 60  # 1h cooldown at 50k+ scale
        elif total_tracked > 10000:
            max_concurrent = 15
            adaptive_cooldown = 30
        elif total_tracked > 5000:
            max_concurrent = 12
            adaptive_cooldown = 20
        elif total_tracked > 1000:
            max_concurrent = 10
            adaptive_cooldown = 15
        else:
            max_concurrent = 10
            adaptive_cooldown = None  # use config default

        user_sem = asyncio.Semaphore(max_concurrent)
        await asyncio.gather(
            *(
                _check_single_user(
                    uuid,
                    min_score,
                    user_sem,
                    adaptive_cooldown,
                    run_anti_abuse=violations_enabled,
                )
                for uuid in affected_user_uuids
            ),
            return_exceptions=True,
        )

        # Periodic cleanup of old violations
        if violations_enabled:
            global _last_violation_cleanup
            if (datetime.utcnow() - _last_violation_cleanup).total_seconds() > 3600:
                retention_days = config_service.get("violation_retention_days", 90)
                cleaned = await db_service.cleanup_old_violations(retention_days)
                if cleaned:
                    logger.info("Cleaned up %d old violations (retention: %d days)", cleaned, retention_days)
                _last_violation_cleanup = datetime.utcnow()

    except Exception as e:
        logger.error("Background violation detection failed: %s", e)


@router.get("/health")
async def collector_health():
    """Health check endpoint."""
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "service": "collector", "database_connected": db_service.is_connected},
    )


@router.get("/stats")
async def collector_stats(request: Request):
    """Collector pipeline metrics — queue depth, processing rates, bottleneck indicators.

    Requires admin JWT token (not agent token) for security.
    """
    # Verify admin auth (collector endpoints skip middleware, so check manually)
    from web.backend.core.security import decode_token
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    payload = decode_token(auth_header[7:], token_type="access")
    if not payload:
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})
    queue_size = len(_pending_violation_users)
    cooldown_size = len(_violation_check_cooldown)
    bg_tasks = len({t for t in _background_tasks if not t.done()})

    # Determine queue health
    if queue_size == 0:
        queue_health = "idle"
    elif queue_size < 500:
        queue_health = "ok"
    elif queue_size < 2000:
        queue_health = "busy"
    else:
        queue_health = "overloaded"

    return JSONResponse(
        status_code=200,
        content={
            "queue": {
                "pending_users": queue_size,
                "peak_queue_size": _stats["peak_queue_size"],
                "health": queue_health,
            },
            "processing": {
                "total_enqueued": _stats["total_enqueued"],
                "total_processed": _stats["total_processed"],
                "total_violations_found": _stats["total_violations_found"],
                "total_skipped_cooldown": _stats["total_skipped_cooldown"],
                "last_drain_duration_ms": _stats["last_drain_duration_ms"],
                "backlog": _stats["total_enqueued"] - _stats["total_processed"],
            },
            "input": {
                "total_batches_received": _stats["total_batches_received"],
                "total_batches_rejected": _stats["total_batches_rejected"],
            },
            "background_tasks": {
                "active": bg_tasks,
                "dropped": _stats["total_tasks_dropped"],
            },
            "cooldown_cache_size": cooldown_size,
            "config": {
                "drain_interval_sec": config_service.get("violation_drain_interval", _VIOLATION_DRAIN_INTERVAL),
                "chunk_size": config_service.get("violation_chunk_size", _VIOLATION_CHUNK_SIZE),
                "cooldown_minutes": config_service.get("violations_check_cooldown_minutes", VIOLATION_CHECK_COOLDOWN_MINUTES),
                "max_background_tasks": config_service.get("violation_max_background_tasks", _MAX_BACKGROUND_TASKS),
            },
            "worker_started_at": _stats["worker_started_at"],
        },
    )
