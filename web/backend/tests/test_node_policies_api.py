"""Tests for node policies API — /api/v2/node-policies/*."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from web.backend.api.deps import get_db


@pytest.mark.asyncio
async def test_upsert_node_policy_success(app, client):
    mock_db = MagicMock()
    mock_db.is_connected = True
    mock_db.upsert_node_network_policy = AsyncMock(return_value={
        "id": 1,
        "node_uuid": "11111111-1111-1111-1111-111111111111",
        "is_enabled": True,
        "expected_connection_types": ["mobile", "mobile_isp"],
        "strict_mode": True,
        "violation_score": 80,
        "reason_template": "LTE only",
        "created_at": None,
        "updated_at": None,
    })
    app.dependency_overrides[get_db] = lambda: mock_db

    response = await client.put(
        "/api/v2/node-policies/11111111-1111-1111-1111-111111111111",
        json={
            "is_enabled": True,
            "expected_connection_types": ["mobile", "mobile_isp"],
            "strict_mode": True,
            "violation_score": 80,
            "reason_template": "LTE only",
        },
    )
    assert response.status_code == 200
    assert response.json()["violation_score"] == 80


@pytest.mark.asyncio
async def test_get_node_policy_success(app, client):
    mock_db = MagicMock()
    mock_db.is_connected = True
    mock_db.get_node_network_policy = AsyncMock(return_value={
        "id": 2,
        "node_uuid": "22222222-2222-2222-2222-222222222222",
        "is_enabled": True,
        "expected_connection_types": ["residential"],
        "strict_mode": False,
        "violation_score": 70,
        "reason_template": None,
        "created_at": None,
        "updated_at": None,
    })
    app.dependency_overrides[get_db] = lambda: mock_db

    response = await client.get("/api/v2/node-policies/22222222-2222-2222-2222-222222222222")
    assert response.status_code == 200
    assert response.json()["expected_connection_types"] == ["residential"]


@pytest.mark.asyncio
async def test_list_node_policies_success(app, client):
    mock_db = MagicMock()
    mock_db.is_connected = True
    mock_db.list_node_network_policies = AsyncMock(return_value=[{
        "id": 1,
        "node_uuid": "33333333-3333-3333-3333-333333333333",
        "is_enabled": True,
        "expected_connection_types": ["vpn"],
        "strict_mode": True,
        "violation_score": 90,
        "reason_template": None,
        "created_at": None,
        "updated_at": None,
    }])
    app.dependency_overrides[get_db] = lambda: mock_db

    response = await client.get("/api/v2/node-policies")
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_delete_node_policy_success(app, client):
    mock_db = MagicMock()
    mock_db.is_connected = True
    mock_db.delete_node_network_policy = AsyncMock(return_value=True)
    app.dependency_overrides[get_db] = lambda: mock_db

    response = await client.delete("/api/v2/node-policies/44444444-4444-4444-4444-444444444444")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_invalid_connection_type_returns_400(client):
    response = await client.put(
        "/api/v2/node-policies/55555555-5555-5555-5555-555555555555",
        json={
            "is_enabled": True,
            "expected_connection_types": ["satellite"],
            "strict_mode": True,
            "violation_score": 80,
        },
    )
    assert response.status_code == 400
