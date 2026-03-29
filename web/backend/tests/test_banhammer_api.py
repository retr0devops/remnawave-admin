"""API tests for Banhammer endpoints under /api/v2/violations/banhammer/*."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web.backend.api.deps import get_db
from shared.config_service import config_service


NOW = datetime(2026, 3, 30, 12, 0, 0, tzinfo=timezone.utc)
USER_UUID = "33333333-3333-3333-3333-333333333333"


def _make_db() -> MagicMock:
    db = MagicMock()
    db.is_connected = True
    db.get_banhammer_events = AsyncMock(return_value=[])
    db.get_banhammer_events_count = AsyncMock(return_value=0)
    db.get_banhammer_states = AsyncMock(return_value=[])
    db.get_banhammer_states_count = AsyncMock(return_value=0)
    return db


@pytest.mark.asyncio
async def test_get_banhammer_settings(app, client):
    store = {
        "banhammer_enabled": True,
        "banhammer_warning_limit": 3,
        "banhammer_warning_cooldown_seconds": 60,
        "banhammer_block_stages_minutes": [15, 60, 360, 720, 1440],
        "banhammer_warning_template": "Banhammer warning template",
    }

    with patch.object(config_service, "get", side_effect=lambda k, d=None: store.get(k, d)):
        resp = await client.get("/api/v2/violations/banhammer/settings")

    assert resp.status_code == 200
    data = resp.json()
    assert data["banhammer_enabled"] is True
    assert data["banhammer_warning_limit"] == 3
    assert data["banhammer_warning_cooldown_seconds"] == 60
    assert data["banhammer_block_stages_minutes"] == [15, 60, 360, 720, 1440]


@pytest.mark.asyncio
async def test_put_banhammer_settings(app, client):
    store = {
        "banhammer_enabled": False,
        "banhammer_warning_limit": 3,
        "banhammer_warning_cooldown_seconds": 60,
        "banhammer_block_stages_minutes": [15, 60, 360, 720, 1440],
        "banhammer_warning_template": "Old warning",
    }

    def _get(key, default=None):
        return store.get(key, default)

    async def _set(key, value):
        store[key] = value
        return True

    with patch.object(config_service, "get", side_effect=_get), \
        patch.object(config_service, "set", side_effect=_set), \
        patch("web.backend.api.v2.violations.write_audit_log", new_callable=AsyncMock):
        resp = await client.put(
            "/api/v2/violations/banhammer/settings",
            json={
                "banhammer_enabled": True,
                "banhammer_warning_limit": 4,
                "banhammer_warning_cooldown_seconds": 120,
                "banhammer_block_stages_minutes": [5, 30, 120],
                "banhammer_warning_template": "New warning",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["banhammer_enabled"] is True
    assert data["banhammer_warning_limit"] == 4
    assert data["banhammer_warning_cooldown_seconds"] == 120
    assert data["banhammer_block_stages_minutes"] == [5, 30, 120]
    assert data["banhammer_warning_template"] == "New warning"


@pytest.mark.asyncio
async def test_put_banhammer_settings_invalid_stages_returns_422(app, client):
    resp = await client.put(
        "/api/v2/violations/banhammer/settings",
        json={
            "banhammer_enabled": True,
            "banhammer_warning_limit": 3,
            "banhammer_warning_cooldown_seconds": 60,
            "banhammer_block_stages_minutes": [15, 0, 120],
            "banhammer_warning_template": "Template",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_banhammer_events(app, client):
    db = _make_db()
    db.get_banhammer_events.return_value = [
        {
            "id": 1,
            "user_uuid": USER_UUID,
            "username": "alice",
            "email": "alice@example.com",
            "event_type": "warn",
            "warning_number": 1,
            "block_stage": 0,
            "block_minutes": None,
            "blocked_until": None,
            "message": "Warning",
            "details": {"reason": "mismatch"},
            "created_at": NOW,
        }
    ]
    db.get_banhammer_events_count.return_value = 1
    app.dependency_overrides[get_db] = lambda: db

    resp = await client.get("/api/v2/violations/banhammer/events?page=1&per_page=20")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["event_type"] == "warn"
    assert data["items"][0]["user_uuid"] == USER_UUID


@pytest.mark.asyncio
async def test_list_banhammer_states_only_blocked(app, client):
    db = _make_db()
    db.get_banhammer_states.return_value = [
        {
            "user_uuid": USER_UUID,
            "username": "alice",
            "email": "alice@example.com",
            "user_status": "DISABLED",
            "warnings_count": 0,
            "block_stage": 2,
            "blocked_until": NOW,
            "pre_block_status": "active",
            "last_warning_at": NOW,
            "created_at": NOW,
            "updated_at": NOW,
            "is_blocked": True,
        }
    ]
    db.get_banhammer_states_count.return_value = 1
    app.dependency_overrides[get_db] = lambda: db

    resp = await client.get("/api/v2/violations/banhammer/states?only_blocked=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["is_blocked"] is True
    assert data["items"][0]["block_stage"] == 2
