"""Tests for node network policies API."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web.backend.api.deps import get_db


NODE_UUID = "11111111-1111-1111-1111-111111111111"
SECOND_NODE_UUID = "22222222-2222-2222-2222-222222222222"
NOW = datetime(2026, 3, 29, 12, 0, 0)


def _policy_row(
    *,
    node_uuid: str = NODE_UUID,
    is_enabled: bool = True,
    expected_connection_types: list[str] | None = None,
    strict_mode: bool = True,
    violation_score: int = 80,
    reason_template: str | None = None,
) -> dict:
    return {
        "id": 1,
        "node_uuid": node_uuid,
        "is_enabled": is_enabled,
        "expected_connection_types": expected_connection_types or ["mobile"],
        "strict_mode": strict_mode,
        "violation_score": violation_score,
        "reason_template": reason_template,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _make_db() -> MagicMock:
    db = MagicMock()
    db.is_connected = True
    db.list_node_network_policies = AsyncMock(return_value=[])
    db.get_node_network_policy = AsyncMock(return_value=None)
    db.get_node_by_uuid = AsyncMock(return_value={"uuid": NODE_UUID})
    db.upsert_node_network_policy = AsyncMock(return_value=None)
    db.delete_node_network_policy = AsyncMock(return_value=True)
    return db


class TestNodePoliciesApi:
    @pytest.mark.asyncio
    async def test_list_policies(self, app, client):
        db = _make_db()
        db.list_node_network_policies.return_value = [
            _policy_row(node_uuid=NODE_UUID, expected_connection_types=["mobile", "mobile_isp"]),
            _policy_row(node_uuid=SECOND_NODE_UUID, is_enabled=False, expected_connection_types=["fixed"]),
        ]
        app.dependency_overrides[get_db] = lambda: db

        resp = await client.get("/api/v2/node-policies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["items"][0]["node_uuid"] == NODE_UUID
        assert data["items"][1]["node_uuid"] == SECOND_NODE_UUID

    @pytest.mark.asyncio
    async def test_get_policy(self, app, client):
        db = _make_db()
        db.get_node_network_policy.return_value = _policy_row(
            node_uuid=NODE_UUID,
            expected_connection_types=["mobile", "mobile_isp"],
            reason_template="LTE-only node",
        )
        app.dependency_overrides[get_db] = lambda: db

        resp = await client.get(f"/api/v2/node-policies/{NODE_UUID}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_uuid"] == NODE_UUID
        assert data["expected_connection_types"] == ["mobile", "mobile_isp"]
        assert data["reason_template"] == "LTE-only node"

    @pytest.mark.asyncio
    async def test_upsert_policy_create_and_update(self, app, client):
        db = _make_db()
        db.upsert_node_network_policy.side_effect = [
            _policy_row(
                node_uuid=NODE_UUID,
                expected_connection_types=["mobile", "mobile_isp"],
                violation_score=80,
                reason_template="Wi-Fi usage is forbidden on LTE node",
            ),
            _policy_row(
                node_uuid=NODE_UUID,
                expected_connection_types=["fixed", "isp"],
                violation_score=70,
                reason_template="Home node policy",
            ),
        ]
        app.dependency_overrides[get_db] = lambda: db

        create_payload = {
            "is_enabled": True,
            "expected_connection_types": ["mobile", "mobile_isp"],
            "strict_mode": True,
            "violation_score": 80,
            "reason_template": "Wi-Fi usage is forbidden on LTE node",
        }
        update_payload = {
            "is_enabled": True,
            "expected_connection_types": ["fixed", "isp"],
            "strict_mode": False,
            "violation_score": 70,
            "reason_template": "Home node policy",
        }

        with patch("web.backend.api.v2.node_policies.write_audit_log", new_callable=AsyncMock):
            create_resp = await client.put(f"/api/v2/node-policies/{NODE_UUID}", json=create_payload)
            update_resp = await client.put(f"/api/v2/node-policies/{NODE_UUID}", json=update_payload)

        assert create_resp.status_code == 200
        assert create_resp.json()["expected_connection_types"] == ["mobile", "mobile_isp"]
        assert update_resp.status_code == 200
        assert update_resp.json()["expected_connection_types"] == ["fixed", "isp"]
        assert db.upsert_node_network_policy.await_count == 2

    @pytest.mark.asyncio
    async def test_delete_policy(self, app, client):
        db = _make_db()
        db.delete_node_network_policy.return_value = True
        app.dependency_overrides[get_db] = lambda: db

        with patch("web.backend.api.v2.node_policies.write_audit_log", new_callable=AsyncMock):
            resp = await client.delete(f"/api/v2/node-policies/{NODE_UUID}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["message"] == "Node policy deleted"

    @pytest.mark.asyncio
    async def test_invalid_connection_type_returns_400(self, app, client):
        db = _make_db()
        db.get_node_by_uuid.return_value = {"uuid": NODE_UUID}
        app.dependency_overrides[get_db] = lambda: db

        resp = await client.put(
            f"/api/v2/node-policies/{NODE_UUID}",
            json={
                "is_enabled": True,
                "expected_connection_types": ["mobile", "bogus_type"],
                "strict_mode": True,
                "violation_score": 80,
            },
        )

        assert resp.status_code == 400
        assert "Unknown connection_type values" in resp.json()["detail"]
        db.upsert_node_network_policy.assert_not_awaited()
