"""Unit tests for NodePolicyAnalyzer and detector integration."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from shared.connection_monitor import ActiveConnection
from shared.geoip import IPMetadata
from shared.violation_detector import NodePolicyAnalyzer, IntelligentViolationDetector, NodePolicyScore


def _conn(ip: str, node_uuid: str | None = "11111111-1111-1111-1111-111111111111") -> ActiveConnection:
    from datetime import datetime
    return ActiveConnection(
        connection_id=1,
        user_uuid="user-1",
        ip_address=ip,
        node_uuid=node_uuid,
        connected_at=datetime.utcnow(),
        device_info=None,
    )


@pytest.mark.asyncio
async def test_policy_absent_score_zero():
    db = MagicMock()
    db.is_connected = True
    db.get_node_network_policy = AsyncMock(return_value=None)
    analyzer = NodePolicyAnalyzer(db)

    result = await analyzer.analyze([_conn("1.1.1.1")], {"1.1.1.1": IPMetadata(ip_address="1.1.1.1", connection_type="residential")})
    assert result.score == 0


@pytest.mark.asyncio
async def test_policy_disabled_score_zero():
    db = MagicMock()
    db.is_connected = True
    db.get_node_network_policy = AsyncMock(return_value={"is_enabled": False, "expected_connection_types": ["mobile"], "violation_score": 80})
    analyzer = NodePolicyAnalyzer(db)

    result = await analyzer.analyze([_conn("1.1.1.1")], {"1.1.1.1": IPMetadata(ip_address="1.1.1.1", connection_type="residential")})
    assert result.score == 0


@pytest.mark.asyncio
async def test_connection_type_missing_score_zero():
    db = MagicMock()
    db.is_connected = True
    db.get_node_network_policy = AsyncMock(return_value={"is_enabled": True, "expected_connection_types": ["mobile"], "violation_score": 80})
    analyzer = NodePolicyAnalyzer(db)

    result = await analyzer.analyze([_conn("1.1.1.1")], {})
    assert result.score == 0


@pytest.mark.asyncio
async def test_connection_type_allowed_score_zero():
    db = MagicMock()
    db.is_connected = True
    db.get_node_network_policy = AsyncMock(return_value={"is_enabled": True, "expected_connection_types": ["mobile"], "violation_score": 80})
    analyzer = NodePolicyAnalyzer(db)

    result = await analyzer.analyze([_conn("1.1.1.1")], {"1.1.1.1": IPMetadata(ip_address="1.1.1.1", connection_type="mobile")})
    assert result.score == 0


@pytest.mark.asyncio
async def test_connection_type_mismatch_adds_score_and_reason():
    db = MagicMock()
    db.is_connected = True
    db.get_node_network_policy = AsyncMock(return_value={"is_enabled": True, "expected_connection_types": ["mobile"], "violation_score": 80})
    analyzer = NodePolicyAnalyzer(db)

    result = await analyzer.analyze([_conn("1.1.1.1")], {"1.1.1.1": IPMetadata(ip_address="1.1.1.1", connection_type="residential")})
    assert result.score == 80
    assert any("Node policy mismatch" in r for r in result.reasons)


@pytest.mark.asyncio
async def test_multiple_connections_no_score_explosion():
    db = MagicMock()
    db.is_connected = True
    db.get_node_network_policy = AsyncMock(return_value={"is_enabled": True, "expected_connection_types": ["mobile"], "violation_score": 75})
    analyzer = NodePolicyAnalyzer(db)

    conns = [_conn("1.1.1.1"), _conn("1.1.1.2")]
    metadata = {
        "1.1.1.1": IPMetadata(ip_address="1.1.1.1", connection_type="residential"),
        "1.1.1.2": IPMetadata(ip_address="1.1.1.2", connection_type="vpn"),
    }
    result = await analyzer.analyze(conns, metadata)
    assert result.score == 75


@pytest.mark.asyncio
async def test_unknown_values_do_not_crash():
    db = MagicMock()
    db.is_connected = True
    db.get_node_network_policy = AsyncMock(return_value={"is_enabled": True, "expected_connection_types": [], "violation_score": 70})
    analyzer = NodePolicyAnalyzer(db)

    result = await analyzer.analyze([_conn("1.1.1.1", node_uuid=None)], {"1.1.1.1": IPMetadata(ip_address="1.1.1.1", connection_type=None)})
    assert result.score == 0


@pytest.mark.asyncio
async def test_detector_integration_node_policy_reason_and_score():
    db = MagicMock()
    db.is_connected = True
    db.get_user_devices_count = AsyncMock(return_value=1)
    db.get_connection_history = AsyncMock(return_value=[])
    db.get_node_network_policy = AsyncMock(return_value={"is_enabled": True, "expected_connection_types": ["mobile"], "violation_score": 80})

    conn_monitor = MagicMock()
    conn_monitor.get_user_active_connections = AsyncMock(return_value=[_conn("8.8.8.8")])

    detector = IntelligentViolationDetector(db, conn_monitor)
    detector.geo_analyzer.geoip.lookup_batch = AsyncMock(return_value={"8.8.8.8": IPMetadata(ip_address="8.8.8.8", connection_type="residential")})
    detector.temporal_analyzer.analyze = MagicMock(return_value=MagicMock(score=0.0, reasons=[], simultaneous_connections_count=0))
    detector.geo_analyzer.analyze = AsyncMock(return_value=MagicMock(score=0.0, reasons=[], countries=set(), cities=set(), impossible_travel_detected=False))
    detector.asn_analyzer.analyze = AsyncMock(return_value=MagicMock(score=0.0, reasons=[], asn_types=set(), is_mobile_carrier=False, is_datacenter=False, is_vpn=False))
    detector.profile_analyzer.analyze = AsyncMock(return_value=MagicMock(score=0.0, reasons=[]))
    detector.device_analyzer.analyze = MagicMock(return_value=MagicMock(score=0.0, reasons=[], unique_fingerprints_count=0, different_os_count=0, os_list=[], client_list=[]))
    detector.hwid_analyzer.analyze = AsyncMock(return_value=MagicMock(score=0.0, reasons=[], shared_hwids_count=0, other_accounts_count=0, matched_details=[]))

    result = await detector.check_user("user-1")
    assert result is not None
    assert isinstance(result.breakdown.get("node_policy"), NodePolicyScore)
    assert result.breakdown["node_policy"].score == 80
    assert any("Node policy mismatch" in r for r in result.reasons)
