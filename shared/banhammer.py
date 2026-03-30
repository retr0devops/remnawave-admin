"""Independent Banhammer pipeline service."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from shared.connection_types import normalize_connection_type
from shared.config_service import config_service
from shared.database import DatabaseService
from shared.logger import logger


@dataclass
class BanhammerSettings:
    enabled: bool = False
    warning_limit: int = 3
    warning_cooldown_seconds: int = 60
    block_stages_minutes: List[int] = field(default_factory=lambda: [15, 60, 360, 720, 1440])
    warning_template: str = (
        "Banhammer warning: node network policy mismatch detected. "
        "Reconnect using an allowed network type."
    )
    support_contact: Optional[str] = None
    block_message_template: str = (
        "Access is temporarily restricted for {ban_minutes} minute(s) due to node network policy mismatch."
    )


@dataclass
class BanhammerProcessResult:
    user_uuid: str
    enabled: bool
    action: str = "noop"
    mismatch_detected: bool = False
    warning_sent: bool = False
    warning_count: int = 0
    warning_limit: int = 3
    is_blocked: bool = False
    block_applied: bool = False
    block_stage: int = 0
    block_minutes: int = 0
    blocked_until: Optional[datetime] = None
    message: Optional[str] = None
    mismatches: List[Dict[str, Any]] = field(default_factory=list)


class BanhammerService:
    """Banhammer engine independent from anti-abuse score/action pipeline."""

    DEFAULT_BLOCK_STAGES_MINUTES = [15, 60, 360, 720, 1440]
    DEFAULT_WARNING_TEMPLATE = (
        "Banhammer warning: node network policy mismatch detected. "
        "Reconnect using an allowed network type."
    )
    DEFAULT_BLOCK_MESSAGE_TEMPLATE = (
        "Access is temporarily restricted for {ban_minutes} minute(s) due to node network policy mismatch."
    )

    def __init__(self, db_service: DatabaseService, api_client_instance: Any = None):
        self.db = db_service
        self._api_client = api_client_instance

    async def process_user(
        self,
        user_uuid: str,
        active_connections: Iterable[Any],
        ip_metadata_cache: Optional[Dict[str, Any]] = None,
    ) -> BanhammerProcessResult:
        """
        Process one user through Banhammer rules.

        Args:
            user_uuid: User UUID.
            active_connections: Active connections list (ActiveConnection or dict-like).
            ip_metadata_cache: Mapping ip -> IPMetadata (or dict with connection_type).
        """
        settings = self.get_settings()
        now = datetime.now(timezone.utc)
        state = await self.db.get_banhammer_state(
            user_uuid,
            create_if_missing=settings.enabled,
        ) or {}

        warnings_count = self._to_int(state.get("warnings_count"), 0, min_value=0)
        block_stage = self._to_int(state.get("block_stage"), 0, min_value=0)
        blocked_until = self._to_datetime(state.get("blocked_until"))
        last_warning_at = self._to_datetime(state.get("last_warning_at"))
        pre_block_status = self._to_text_or_none(state.get("pre_block_status"))

        # Auto-unblock after Banhammer block expiry.
        if blocked_until and blocked_until <= now:
            restored = await self._restore_user_after_block(user_uuid, pre_block_status)
            if restored:
                await self.db.upsert_banhammer_state(
                    user_uuid=user_uuid,
                    warnings_count=0,
                    block_stage=block_stage,
                    blocked_until=None,
                    pre_block_status=None,
                    last_warning_at=None,
                )
                await self.db.add_banhammer_event(
                    user_uuid=user_uuid,
                    event_type="unblock",
                    block_stage=block_stage,
                    message="Banhammer block expired, user restored",
                    details={"reason": "block_expired"},
                )
                blocked_until = None
                pre_block_status = None
                warnings_count = 0
            else:
                return BanhammerProcessResult(
                    user_uuid=user_uuid,
                    enabled=settings.enabled,
                    action="unblock_failed",
                    is_blocked=True,
                    blocked_until=blocked_until,
                    message="Failed to auto-unblock user",
                )

        if blocked_until and blocked_until > now:
            return BanhammerProcessResult(
                user_uuid=user_uuid,
                enabled=settings.enabled,
                action="blocked",
                is_blocked=True,
                blocked_until=blocked_until,
                block_stage=block_stage,
                message="User is currently blocked by Banhammer",
            )

        if not settings.enabled:
            return BanhammerProcessResult(
                user_uuid=user_uuid,
                enabled=False,
                action="disabled",
                warning_count=warnings_count,
                warning_limit=settings.warning_limit,
            )

        mismatches = await self._detect_node_policy_mismatches(
            active_connections=active_connections,
            ip_metadata_cache=ip_metadata_cache or {},
        )
        if not mismatches:
            if warnings_count > 0:
                await self.db.upsert_banhammer_state(
                    user_uuid=user_uuid,
                    warnings_count=0,
                    block_stage=block_stage,
                    blocked_until=None,
                    pre_block_status=None,
                    last_warning_at=last_warning_at,
                )
                warnings_count = 0
            return BanhammerProcessResult(
                user_uuid=user_uuid,
                enabled=True,
                action="no_mismatch",
                warning_count=warnings_count,
                warning_limit=settings.warning_limit,
            )

        # Cooldown for warnings.
        if last_warning_at and (now - last_warning_at).total_seconds() < settings.warning_cooldown_seconds:
            return BanhammerProcessResult(
                user_uuid=user_uuid,
                enabled=True,
                action="cooldown",
                mismatch_detected=True,
                warning_count=warnings_count,
                warning_limit=settings.warning_limit,
                mismatches=mismatches,
                message="Warning cooldown is active",
            )

        warnings_count += 1
        await self.db.upsert_banhammer_state(
            user_uuid=user_uuid,
            warnings_count=warnings_count,
            block_stage=block_stage,
            blocked_until=None,
            pre_block_status=None,
            last_warning_at=now,
        )
        await self.db.add_banhammer_event(
            user_uuid=user_uuid,
            event_type="warn",
            warning_number=warnings_count,
            block_stage=block_stage,
            message=settings.warning_template,
            details={
                "warning_limit": settings.warning_limit,
                "warning_cooldown_seconds": settings.warning_cooldown_seconds,
                "mismatches": mismatches,
            },
        )

        # Warning path (still below threshold).
        if warnings_count < settings.warning_limit:
            return BanhammerProcessResult(
                user_uuid=user_uuid,
                enabled=True,
                action="warn",
                mismatch_detected=True,
                warning_sent=True,
                warning_count=warnings_count,
                warning_limit=settings.warning_limit,
                mismatches=mismatches,
                message=settings.warning_template,
            )

        # Threshold reached: block user on stage.
        stage_idx = min(max(0, block_stage), len(settings.block_stages_minutes) - 1)
        block_minutes = settings.block_stages_minutes[stage_idx]
        next_stage = min(stage_idx + 1, len(settings.block_stages_minutes) - 1)
        blocked_until = now + timedelta(minutes=block_minutes)
        block_message = self._render_block_message(
            template=settings.block_message_template,
            block_minutes=block_minutes,
            block_stage=stage_idx + 1,
            support_contact=settings.support_contact,
        )

        user = await self.db.get_user_by_uuid(user_uuid)
        current_status = self._extract_user_status(user)
        should_disable = current_status != "disabled"

        if should_disable:
            try:
                api_client = self._get_api_client()
                await api_client.disable_user(user_uuid)
            except Exception as e:
                logger.error("Banhammer failed to disable user %s: %s", user_uuid, e, exc_info=True)
                return BanhammerProcessResult(
                    user_uuid=user_uuid,
                    enabled=True,
                    action="block_failed",
                    mismatch_detected=True,
                    warning_count=warnings_count,
                    warning_limit=settings.warning_limit,
                    mismatches=mismatches,
                    message=f"Failed to block user: {e}",
                )

        await self.db.upsert_banhammer_state(
            user_uuid=user_uuid,
            warnings_count=0,
            block_stage=next_stage,
            blocked_until=blocked_until,
            pre_block_status=current_status,
            last_warning_at=now,
        )
        await self.db.add_banhammer_event(
            user_uuid=user_uuid,
            event_type="block",
            warning_number=warnings_count,
            block_stage=stage_idx + 1,
            block_minutes=block_minutes,
            blocked_until=blocked_until,
            message=block_message,
            details={
                "block_message_template": settings.block_message_template,
                "block_message_rendered": block_message,
                "mismatches": mismatches,
                "pre_block_status": current_status,
                "stage_index": stage_idx,
                "next_stage_index": next_stage,
            },
        )

        logger.info(
            "Banhammer block applied: user=%s stage=%d duration=%dm",
            user_uuid,
            stage_idx + 1,
            block_minutes,
        )

        return BanhammerProcessResult(
            user_uuid=user_uuid,
            enabled=True,
            action="block",
            mismatch_detected=True,
            warning_sent=True,
            warning_count=0,
            warning_limit=settings.warning_limit,
            is_blocked=True,
            block_applied=True,
            block_stage=stage_idx + 1,
            block_minutes=block_minutes,
            blocked_until=blocked_until,
            mismatches=mismatches,
            message=block_message,
        )

    async def reset_user(self, user_uuid: str, reason: str = "manual_reset") -> bool:
        """Reset Banhammer counters for one user (and unblock if needed)."""
        state = await self.db.get_banhammer_state(user_uuid, create_if_missing=False)
        if not state:
            return True

        blocked_until = self._to_datetime(state.get("blocked_until"))
        pre_block_status = self._to_text_or_none(state.get("pre_block_status"))
        now = datetime.now(timezone.utc)

        if blocked_until and blocked_until > now:
            restored = await self._restore_user_after_block(user_uuid, pre_block_status)
            if not restored:
                return False

        ok = await self.db.reset_banhammer_state(user_uuid)
        if ok:
            await self.db.add_banhammer_event(
                user_uuid=user_uuid,
                event_type="reset",
                message="Banhammer state reset",
                details={"reason": reason},
            )
        return ok

    def get_settings(self) -> BanhammerSettings:
        """Read Banhammer settings from config service with safe normalization."""
        enabled = self._to_bool(config_service.get("banhammer_enabled", False), False)
        warning_limit = self._to_int(config_service.get("banhammer_warning_limit", 3), 3, min_value=1)
        warning_cooldown_seconds = self._to_int(
            config_service.get("banhammer_warning_cooldown_seconds", 60),
            60,
            min_value=1,
        )
        block_stages_minutes = self._parse_stage_minutes(
            config_service.get("banhammer_block_stages_minutes", self.DEFAULT_BLOCK_STAGES_MINUTES)
        )
        warning_template = self._to_text_or_none(config_service.get("banhammer_warning_template", None))
        if not warning_template:
            warning_template = self.DEFAULT_WARNING_TEMPLATE

        support_contact = self._to_text_or_none(config_service.get("banhammer_support_contact", None))
        block_message_template = self._to_text_or_none(
            config_service.get("banhammer_block_message_template", self.DEFAULT_BLOCK_MESSAGE_TEMPLATE)
        )
        if not block_message_template:
            block_message_template = self.DEFAULT_BLOCK_MESSAGE_TEMPLATE

        return BanhammerSettings(
            enabled=enabled,
            warning_limit=warning_limit,
            warning_cooldown_seconds=warning_cooldown_seconds,
            block_stages_minutes=block_stages_minutes,
            warning_template=warning_template,
            support_contact=support_contact,
            block_message_template=block_message_template,
        )

    async def _detect_node_policy_mismatches(
        self,
        active_connections: Iterable[Any],
        ip_metadata_cache: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if not self.db.is_connected:
            return []

        node_to_ips: Dict[str, set[str]] = {}
        for conn in active_connections or []:
            node_uuid = self._extract_node_uuid(conn)
            ip_address = self._extract_ip(conn)
            if not node_uuid or not ip_address:
                continue
            node_to_ips.setdefault(node_uuid, set()).add(ip_address)

        if not node_to_ips:
            return []

        policies = await self.db.get_node_network_policies_by_node_uuids(list(node_to_ips.keys()))
        if not policies:
            return []

        mismatches: List[Dict[str, Any]] = []
        for node_uuid, ips in sorted(node_to_ips.items()):
            policy = policies.get(node_uuid)
            if not policy or not policy.get("is_enabled", True):
                continue

            expected = self._normalize_expected_types(policy.get("expected_connection_types"))
            if not expected:
                continue

            actual_mismatch: set[str] = set()
            for ip_address in ips:
                connection_type = self._extract_connection_type(ip_metadata_cache.get(ip_address))
                if not connection_type:
                    continue
                if connection_type not in expected:
                    actual_mismatch.add(connection_type)

            if actual_mismatch:
                mismatches.append(
                    {
                        "node_uuid": node_uuid,
                        "expected_connection_types": sorted(expected),
                        "actual_connection_types": sorted(actual_mismatch),
                    }
                )

        return mismatches

    async def _restore_user_after_block(self, user_uuid: str, pre_block_status: Optional[str]) -> bool:
        """
        Restore user after Banhammer block.

        We only issue enable call when user was ACTIVE before Banhammer block.
        """
        normalized = (pre_block_status or "").strip().lower()
        if normalized and normalized not in {"active", "enabled"}:
            return True

        try:
            api_client = self._get_api_client()
            await api_client.enable_user(user_uuid)
            return True
        except Exception as e:
            logger.error("Banhammer failed to enable user %s: %s", user_uuid, e, exc_info=True)
            return False

    def _get_api_client(self) -> Any:
        if self._api_client is not None:
            return self._api_client
        from shared.api_client import api_client
        return api_client

    @staticmethod
    def _extract_node_uuid(conn: Any) -> Optional[str]:
        value = conn.get("node_uuid") if isinstance(conn, dict) else getattr(conn, "node_uuid", None)
        value = str(value).strip() if value else ""
        return value or None

    @staticmethod
    def _extract_ip(conn: Any) -> Optional[str]:
        value = conn.get("ip_address") if isinstance(conn, dict) else getattr(conn, "ip_address", None)
        value = str(value).strip() if value else ""
        return value or None

    @staticmethod
    def _extract_connection_type(metadata: Any) -> Optional[str]:
        if metadata is None:
            return None
        if isinstance(metadata, dict):
            raw = metadata.get("connection_type")
        else:
            raw = getattr(metadata, "connection_type", None)
        return normalize_connection_type(raw)

    @staticmethod
    def _normalize_expected_types(raw_values: Any) -> set[str]:
        values = raw_values
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except Exception:
                values = []
        if not isinstance(values, list):
            values = []
        normalized: set[str] = set()
        for value in values:
            item = normalize_connection_type(value)
            if item:
                normalized.add(item)
        return normalized

    @staticmethod
    def _extract_user_status(user: Optional[Dict[str, Any]]) -> str:
        if not user:
            return "active"
        raw = user.get("status")
        if raw is None and isinstance(user.get("response"), dict):
            raw = user["response"].get("status")
        normalized = (str(raw).strip().lower() if raw is not None else "")
        return normalized or "active"

    @staticmethod
    def _to_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return default

    @staticmethod
    def _to_int(value: Any, default: int, min_value: Optional[int] = None) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        if min_value is not None:
            parsed = max(min_value, parsed)
        return parsed

    @classmethod
    def _parse_stage_minutes(cls, raw: Any) -> List[int]:
        value = raw
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                value = []
        if not isinstance(value, list):
            value = []

        parsed: List[int] = []
        for item in value:
            try:
                minutes = int(item)
            except (TypeError, ValueError):
                continue
            if minutes > 0:
                parsed.append(minutes)

        if not parsed:
            return list(cls.DEFAULT_BLOCK_STAGES_MINUTES)
        return parsed

    @staticmethod
    def _to_datetime(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except ValueError:
                return None
        return None

    @staticmethod
    def _to_text_or_none(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _render_block_message(
        template: str,
        block_minutes: int,
        block_stage: int,
        support_contact: Optional[str] = None,
    ) -> str:
        base = (template or "").strip() or BanhammerService.DEFAULT_BLOCK_MESSAGE_TEMPLATE
        contact = (support_contact or "").strip()
        replacements = {
            "{ban_minutes}": str(block_minutes),
            "{block_stage}": str(block_stage),
            "{support_contact}": contact,
        }
        rendered = base
        for token, value in replacements.items():
            rendered = rendered.replace(token, value)
        return rendered.strip() or BanhammerService.DEFAULT_BLOCK_MESSAGE_TEMPLATE
