"""Tests for NodePolicyAnalyzer."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.connection_monitor import ActiveConnection
from shared.geoip import IPMetadata
from shared.violation_detector import NodePolicyAnalyzer, NodePolicyScore


NODE_UUID = "11111111-1111-1111-1111-111111111111"
NOW = datetime(2026, 3, 29, 12, 0, 0)


def _make_db(policies: dict[str, dict] | None) -> MagicMock:
    db = MagicMock()
    db.is_connected = True
    db.get_node_network_policies_by_node_uuids = AsyncMock(return_value=policies or {})
    return db


def _make_connection(ip: str, node_uuid: str = NODE_UUID) -> ActiveConnection:
    return ActiveConnection(
        connection_id=1,
        user_uuid="user-1",
        ip_address=ip,
        node_uuid=node_uuid,
        connected_at=NOW,
        device_info=None,
    )


def _make_metadata(ip: str, connection_type: str | None) -> IPMetadata:
    return IPMetadata(ip=ip, connection_type=connection_type)


class TestNodePolicyAnalyzer:
    @pytest.mark.asyncio
    async def test_no_policy_returns_zero(self):
        analyzer = NodePolicyAnalyzer(_make_db({}))
        conn = _make_connection("203.0.113.10")
        result = await analyzer.analyze([conn], {conn.ip_address: _make_metadata(conn.ip_address, "residential")})

        assert result.score == 0.0
        assert result.reasons == []
        assert result.mismatched_nodes_count == 0

    @pytest.mark.asyncio
    async def test_disabled_policy_returns_zero(self):
        analyzer = NodePolicyAnalyzer(
            _make_db(
                {
                    NODE_UUID: {
                        "is_enabled": False,
                        "expected_connection_types": ["mobile"],
                        "violation_score": 90,
                    }
                }
            )
        )
        conn = _make_connection("203.0.113.11")
        result = await analyzer.analyze([conn], {conn.ip_address: _make_metadata(conn.ip_address, "residential")})

        assert result.score == 0.0
        assert result.reasons == []
        assert result.mismatched_nodes_count == 0

    @pytest.mark.asyncio
    async def test_missing_connection_type_returns_zero(self):
        analyzer = NodePolicyAnalyzer(
            _make_db(
                {
                    NODE_UUID: {
                        "is_enabled": True,
                        "expected_connection_types": ["mobile"],
                        "violation_score": 80,
                    }
                }
            )
        )
        conn = _make_connection("203.0.113.12")
        result = await analyzer.analyze([conn], {})

        assert result.score == 0.0
        assert result.reasons == []
        assert result.mismatched_nodes_count == 0

    @pytest.mark.asyncio
    async def test_allowed_type_returns_zero(self):
        analyzer = NodePolicyAnalyzer(
            _make_db(
                {
                    NODE_UUID: {
                        "is_enabled": True,
                        "expected_connection_types": ["residential", "fixed"],
                        "violation_score": 80,
                    }
                }
            )
        )
        conn = _make_connection("203.0.113.13")
        result = await analyzer.analyze([conn], {conn.ip_address: _make_metadata(conn.ip_address, "residential")})

        assert result.score == 0.0
        assert result.reasons == []
        assert result.mismatched_nodes_count == 0

    @pytest.mark.asyncio
    async def test_mismatch_adds_score_and_reason(self):
        analyzer = NodePolicyAnalyzer(
            _make_db(
                {
                    NODE_UUID: {
                        "is_enabled": True,
                        "expected_connection_types": ["mobile", "mobile_isp"],
                        "violation_score": 80,
                        "reason_template": "Wi-Fi usage is forbidden on LTE node",
                    }
                }
            )
        )
        conn = _make_connection("203.0.113.14")
        result = await analyzer.analyze([conn], {conn.ip_address: _make_metadata(conn.ip_address, "residential")})

        assert result.score == 80.0
        assert result.mismatched_nodes_count == 1
        assert len(result.reasons) == 1
        assert "Node policy mismatch: node=" in result.reasons[0]
        assert "expected=[mobile, mobile_isp]" in result.reasons[0]
        assert "actual=residential" in result.reasons[0]
        assert result.reasons[0].startswith("Wi-Fi usage is forbidden on LTE node | ")

    @pytest.mark.asyncio
    async def test_multiple_connections_are_deduplicated_per_node(self):
        analyzer = NodePolicyAnalyzer(
            _make_db(
                {
                    NODE_UUID: {
                        "is_enabled": True,
                        "expected_connection_types": ["mobile"],
                        "violation_score": 70,
                    }
                }
            )
        )
        connections = [
            _make_connection("203.0.113.15"),
            _make_connection("203.0.113.16"),
        ]
        metadata = {
            "203.0.113.15": _make_metadata("203.0.113.15", "residential"),
            "203.0.113.16": _make_metadata("203.0.113.16", "residential"),
        }

        result = await analyzer.analyze(connections, metadata)

        assert result.score == 70.0
        assert result.mismatched_nodes_count == 1
        assert len(result.reasons) == 1

    @pytest.mark.asyncio
    async def test_invalid_and_empty_values_are_stable(self):
        analyzer = NodePolicyAnalyzer(
            _make_db(
                {
                    NODE_UUID: {
                        "is_enabled": True,
                        "expected_connection_types": "not-json",
                        "strict_mode": "yes",
                        "violation_score": 55,
                    }
                }
            )
        )
        conn = _make_connection("203.0.113.17")
        metadata = {conn.ip_address: _make_metadata(conn.ip_address, None)}

        result1 = await analyzer.analyze([conn], metadata)
        result2 = await analyzer.analyze([conn], metadata)

        assert result1 == result2
        assert isinstance(result1, NodePolicyScore)
        assert result1.score == 0.0
        assert result1.reasons == []
        assert result1.mismatched_nodes_count == 0
