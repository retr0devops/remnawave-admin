"""Unit tests for BanhammerService."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.banhammer import BanhammerService
from shared.connection_monitor import ActiveConnection
from shared.geoip import IPMetadata


USER_UUID = "33333333-3333-3333-3333-333333333333"
NODE_UUID = "11111111-1111-1111-1111-111111111111"
NOW = datetime(2026, 3, 30, 12, 0, 0, tzinfo=timezone.utc)


def _conn(ip: str, node_uuid: str = NODE_UUID) -> ActiveConnection:
    return ActiveConnection(
        connection_id=1,
        user_uuid=USER_UUID,
        ip_address=ip,
        node_uuid=node_uuid,
        connected_at=NOW,
        device_info=None,
    )


def _meta(ip: str, connection_type: str | None) -> IPMetadata:
    return IPMetadata(ip=ip, connection_type=connection_type)


def _policy(
    *,
    is_enabled: bool = True,
    expected_connection_types: list[str] | str | None = None,
) -> dict:
    return {
        "node_uuid": NODE_UUID,
        "is_enabled": is_enabled,
        "expected_connection_types": expected_connection_types if expected_connection_types is not None else ["mobile"],
        "strict_mode": True,
        "violation_score": 80,
    }


def _db_mock() -> MagicMock:
    db = MagicMock()
    db.is_connected = True
    db.get_banhammer_state = AsyncMock(return_value=None)
    db.upsert_banhammer_state = AsyncMock(return_value={})
    db.add_banhammer_event = AsyncMock(return_value={})
    db.get_node_network_policies_by_node_uuids = AsyncMock(return_value={})
    db.get_user_by_uuid = AsyncMock(return_value={"status": "ACTIVE"})
    db.reset_banhammer_state = AsyncMock(return_value=True)
    return db


def _config_side_effect(key: str, default=None):
    values = {
        "banhammer_enabled": True,
        "banhammer_warning_limit": 3,
        "banhammer_warning_cooldown_seconds": 60,
        "banhammer_block_stages_minutes": [15, 60, 360, 720, 1440],
        "banhammer_warning_template": "Banhammer warning template",
    }
    return values.get(key, default)


@pytest.mark.asyncio
async def test_no_policy_returns_no_mismatch():
    db = _db_mock()
    service = BanhammerService(db, api_client_instance=MagicMock())
    connections = [_conn("203.0.113.10")]
    metadata = {"203.0.113.10": _meta("203.0.113.10", "residential")}

    with patch("shared.banhammer.config_service.get", side_effect=_config_side_effect):
        result = await service.process_user(USER_UUID, connections, metadata)

    assert result.action == "no_mismatch"
    assert result.warning_sent is False
    db.upsert_banhammer_state.assert_not_awaited()
    db.add_banhammer_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_policy_returns_no_mismatch():
    db = _db_mock()
    db.get_node_network_policies_by_node_uuids.return_value = {NODE_UUID: _policy(is_enabled=False)}
    service = BanhammerService(db, api_client_instance=MagicMock())
    connections = [_conn("203.0.113.11")]
    metadata = {"203.0.113.11": _meta("203.0.113.11", "residential")}

    with patch("shared.banhammer.config_service.get", side_effect=_config_side_effect):
        result = await service.process_user(USER_UUID, connections, metadata)

    assert result.action == "no_mismatch"
    assert result.warning_sent is False
    db.add_banhammer_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_connection_type_returns_no_mismatch():
    db = _db_mock()
    db.get_node_network_policies_by_node_uuids.return_value = {NODE_UUID: _policy(expected_connection_types=["mobile"])}
    service = BanhammerService(db, api_client_instance=MagicMock())
    connections = [_conn("203.0.113.12")]

    with patch("shared.banhammer.config_service.get", side_effect=_config_side_effect):
        result = await service.process_user(USER_UUID, connections, {})

    assert result.action == "no_mismatch"
    assert result.warning_sent is False
    db.add_banhammer_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_allowed_connection_type_returns_no_mismatch():
    db = _db_mock()
    db.get_node_network_policies_by_node_uuids.return_value = {
        NODE_UUID: _policy(expected_connection_types=["mobile", "residential"])
    }
    service = BanhammerService(db, api_client_instance=MagicMock())
    connections = [_conn("203.0.113.13")]
    metadata = {"203.0.113.13": _meta("203.0.113.13", "residential")}

    with patch("shared.banhammer.config_service.get", side_effect=_config_side_effect):
        result = await service.process_user(USER_UUID, connections, metadata)

    assert result.action == "no_mismatch"
    assert result.warning_sent is False
    db.add_banhammer_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_mismatch_adds_warning():
    db = _db_mock()
    db.get_banhammer_state.return_value = {
        "warnings_count": 0,
        "block_stage": 0,
        "blocked_until": None,
        "pre_block_status": None,
        "last_warning_at": None,
    }
    db.get_node_network_policies_by_node_uuids.return_value = {NODE_UUID: _policy(expected_connection_types=["mobile"])}

    service = BanhammerService(db, api_client_instance=MagicMock())
    connections = [_conn("203.0.113.14")]
    metadata = {"203.0.113.14": _meta("203.0.113.14", "residential")}

    with patch("shared.banhammer.config_service.get", side_effect=_config_side_effect):
        result = await service.process_user(USER_UUID, connections, metadata)

    assert result.action == "warn"
    assert result.warning_sent is True
    assert result.warning_count == 1
    db.upsert_banhammer_state.assert_awaited()
    db.add_banhammer_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_warning_limit_triggers_block_and_next_stage():
    db = _db_mock()
    db.get_banhammer_state.return_value = {
        "warnings_count": 2,
        "block_stage": 0,
        "blocked_until": None,
        "pre_block_status": None,
        "last_warning_at": None,
    }
    db.get_node_network_policies_by_node_uuids.return_value = {NODE_UUID: _policy(expected_connection_types=["mobile"])}
    api_client = MagicMock()
    api_client.disable_user = AsyncMock(return_value={})
    api_client.enable_user = AsyncMock(return_value={})
    service = BanhammerService(db, api_client_instance=api_client)

    connections = [_conn("203.0.113.15")]
    metadata = {"203.0.113.15": _meta("203.0.113.15", "residential")}

    with patch("shared.banhammer.config_service.get", side_effect=_config_side_effect):
        result = await service.process_user(USER_UUID, connections, metadata)

    assert result.action == "block"
    assert result.block_applied is True
    assert result.block_minutes == 15
    assert result.block_stage == 1
    assert result.is_blocked is True
    api_client.disable_user.assert_awaited_once_with(USER_UUID)
    assert db.add_banhammer_event.await_count == 2


@pytest.mark.asyncio
async def test_cooldown_skips_warning():
    current = datetime.now(timezone.utc)
    db = _db_mock()
    db.get_banhammer_state.return_value = {
        "warnings_count": 1,
        "block_stage": 0,
        "blocked_until": None,
        "pre_block_status": None,
        "last_warning_at": current - timedelta(seconds=30),
    }
    db.get_node_network_policies_by_node_uuids.return_value = {NODE_UUID: _policy(expected_connection_types=["mobile"])}
    service = BanhammerService(db, api_client_instance=MagicMock())
    connections = [_conn("203.0.113.16")]
    metadata = {"203.0.113.16": _meta("203.0.113.16", "residential")}

    with patch("shared.banhammer.config_service.get", side_effect=_config_side_effect):
        result = await service.process_user(USER_UUID, connections, metadata)

    assert result.action == "cooldown"
    assert result.warning_sent is False
    assert result.warning_count == 1
    db.upsert_banhammer_state.assert_not_awaited()
    db.add_banhammer_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_block_auto_unblocks():
    current = datetime.now(timezone.utc)
    db = _db_mock()
    db.get_banhammer_state.return_value = {
        "warnings_count": 0,
        "block_stage": 1,
        "blocked_until": current - timedelta(minutes=1),
        "pre_block_status": "active",
        "last_warning_at": None,
    }
    db.get_node_network_policies_by_node_uuids.return_value = {}
    api_client = MagicMock()
    api_client.disable_user = AsyncMock(return_value={})
    api_client.enable_user = AsyncMock(return_value={})
    service = BanhammerService(db, api_client_instance=api_client)

    with patch("shared.banhammer.config_service.get", side_effect=_config_side_effect):
        result = await service.process_user(USER_UUID, [], {})

    assert result.action == "no_mismatch"
    api_client.enable_user.assert_awaited_once_with(USER_UUID)
    db.add_banhammer_event.assert_awaited()
    event_call = db.add_banhammer_event.await_args_list[0]
    assert event_call.kwargs["event_type"] == "unblock"


@pytest.mark.asyncio
async def test_unknown_or_empty_values_are_stable():
    db = _db_mock()
    db.get_banhammer_state.return_value = {
        "warnings_count": "abc",
        "block_stage": "zzz",
        "blocked_until": None,
        "pre_block_status": None,
        "last_warning_at": None,
    }
    db.get_node_network_policies_by_node_uuids.return_value = {
        NODE_UUID: _policy(expected_connection_types="not-json")
    }
    service = BanhammerService(db, api_client_instance=MagicMock())
    connections = [_conn("203.0.113.17")]
    metadata = {"203.0.113.17": _meta("203.0.113.17", None)}

    with patch("shared.banhammer.config_service.get", side_effect=_config_side_effect):
        result = await service.process_user(USER_UUID, connections, metadata)

    assert result.action == "no_mismatch"
    assert result.warning_sent is False
