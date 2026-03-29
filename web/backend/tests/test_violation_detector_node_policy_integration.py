"""Integration-style tests for node policy support in IntelligentViolationDetector."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.connection_monitor import ActiveConnection
from shared.config_service import config_service
from shared.geoip import IPMetadata
from shared.violation_detector import (
    ASNScore,
    DeviceScore,
    GeoScore,
    HwidScore,
    IntelligentViolationDetector,
    NodePolicyScore,
    ProfileScore,
    TemporalScore,
)


USER_UUID = "33333333-3333-3333-3333-333333333333"
NODE_UUID = "11111111-1111-1111-1111-111111111111"
IP_ADDRESS = "203.0.113.99"
NOW = datetime(2026, 3, 29, 12, 0, 0)


def _policy_row() -> dict:
    return {
        "id": 1,
        "node_uuid": NODE_UUID,
        "is_enabled": True,
        "expected_connection_types": ["mobile", "mobile_isp"],
        "strict_mode": True,
        "violation_score": 80,
        "reason_template": "Wi-Fi usage is forbidden on LTE node",
        "created_at": NOW,
        "updated_at": NOW,
    }


@pytest.mark.asyncio
async def test_detector_reports_node_policy_mismatch():
    db = MagicMock()
    db.is_connected = True
    db.get_user_devices_count = AsyncMock(return_value=1)
    db.get_connection_history = AsyncMock(return_value=[])
    db.get_recent_violations_count = AsyncMock(return_value=0)
    db.get_node_network_policies_by_node_uuids = AsyncMock(return_value={NODE_UUID: _policy_row()})

    connection_monitor = MagicMock()
    connection_monitor.get_user_active_connections = AsyncMock(
        return_value=[
            ActiveConnection(
                connection_id=1,
                user_uuid=USER_UUID,
                ip_address=IP_ADDRESS,
                node_uuid=NODE_UUID,
                connected_at=NOW,
                device_info=None,
            )
        ]
    )

    geoip_service = MagicMock()
    geoip_service.lookup_batch = AsyncMock(
        return_value={
            IP_ADDRESS: IPMetadata(ip=IP_ADDRESS, connection_type="residential"),
        }
    )

    detector = IntelligentViolationDetector(db, connection_monitor, geoip_service=geoip_service)

    with patch.object(config_service, "get", side_effect=lambda key, default=None: default), \
        patch.object(detector.temporal_analyzer, "analyze", return_value=TemporalScore(score=0.0, reasons=[], simultaneous_connections_count=0)), \
        patch.object(detector.geo_analyzer, "analyze", new=AsyncMock(return_value=GeoScore(score=0.0, reasons=[], countries=set(), cities=set()))), \
        patch.object(detector.asn_analyzer, "analyze", new=AsyncMock(return_value=ASNScore(score=0.0, reasons=[], asn_types=set()))), \
        patch.object(detector.profile_analyzer, "analyze", new=AsyncMock(return_value=ProfileScore(score=0.0, reasons=[]))), \
        patch.object(detector.device_analyzer, "analyze", return_value=DeviceScore(score=0.0, reasons=[], unique_fingerprints_count=0, different_os_count=0)), \
        patch.object(detector.hwid_analyzer, "analyze", new=AsyncMock(return_value=HwidScore(score=0.0, reasons=[]))):
        result = await detector.check_user(USER_UUID)

    assert result is not None
    assert result.total == 80.0
    assert result.breakdown["node_policy"].score == 80.0
    assert result.breakdown["node_policy"].mismatched_nodes_count == 1
    assert any("Node policy mismatch:" in reason for reason in result.reasons)
    assert any("actual=residential" in reason for reason in result.reasons)
    assert result.recommended_action.value == "temp_block"
