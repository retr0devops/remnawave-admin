"""Tests for API keys — /api/v2/api-keys/*."""
import pytest
from datetime import datetime
from unittest.mock import patch, AsyncMock, MagicMock

from web.backend.api.deps import get_current_admin
from .conftest import make_admin


MOCK_API_KEY_ROW = {
    "id": 1,
    "name": "Test Key",
    "key_prefix": "rw_abc123",
    "scopes": ["users:read", "nodes:read"],
    "is_active": True,
    "expires_at": None,
    "last_used_at": None,
    "created_by_username": "admin",
    "created_at": datetime(2026, 1, 15, 10, 0, 0),
}

MOCK_API_KEY_CREATED = {
    **MOCK_API_KEY_ROW,
    "scopes": ["users:read"],
}


def _mock_db_service(fetch_return=None, fetchrow_return=None, fetchval_return=None, execute_return="DELETE 1"):
    """Create a mock db_service with acquire() context manager."""
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=fetch_return or [])
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    mock_conn.fetchval = AsyncMock(return_value=fetchval_return)
    mock_conn.execute = AsyncMock(return_value=execute_return)

    mock_svc = MagicMock()
    mock_svc.is_connected = True
    mock_svc.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_svc.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_svc, mock_conn


class TestApiStatus:
    """GET /api/v2/api-keys/status."""

    @pytest.mark.asyncio
    async def test_status_returns_settings(self, client):
        resp = await client.get("/api/v2/api-keys/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "external_api_enabled" in data
        assert "external_api_docs" in data


class TestListApiKeys:
    """GET /api/v2/api-keys/."""

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_list_keys_success(self, mock_db_svc, client):
        mock_svc, _ = _mock_db_service(fetch_return=[MOCK_API_KEY_ROW])
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.get("/api/v2/api-keys/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Test Key"
        assert data[0]["key_prefix"] == "rw_abc123"

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_list_keys_empty(self, mock_db_svc, client):
        mock_svc, _ = _mock_db_service(fetch_return=[])
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.get("/api/v2/api-keys/")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_list_keys_db_disconnected(self, mock_db_svc, client):
        mock_db_svc.is_connected = False
        resp = await client.get("/api/v2/api-keys/")
        assert resp.status_code == 200
        assert resp.json() == []


class TestListScopes:
    """GET /api/v2/api-keys/scopes."""

    @pytest.mark.asyncio
    async def test_list_scopes(self, client):
        resp = await client.get("/api/v2/api-keys/scopes")
        assert resp.status_code == 200
        data = resp.json()
        assert "scopes" in data
        assert "users:read" in data["scopes"]
        assert "nodes:read" in data["scopes"]


class TestCreateApiKey:
    """POST /api/v2/api-keys/."""

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    @patch("web.backend.core.api_key_auth.create_api_key_record", new_callable=AsyncMock)
    async def test_create_key_success(self, mock_create, mock_db_svc, client):
        mock_svc, _ = _mock_db_service()
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        mock_create.return_value = ("rw_full_raw_key_here", {
            "id": 2, "name": "New Key", "key_prefix": "rw_newk",
            "scopes": ["users:read"], "is_active": True,
            "expires_at": None, "last_used_at": None,
            "created_by_username": "superadmin_user",
            "created_at": "2026-03-06T12:00:00",
        })

        resp = await client.post("/api/v2/api-keys/", json={
            "name": "New Key",
            "scopes": ["users:read"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["raw_key"] == "rw_full_raw_key_here"
        assert data["name"] == "New Key"

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_create_key_invalid_scope(self, mock_db_svc, client):
        mock_svc, _ = _mock_db_service()
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.post("/api/v2/api-keys/", json={
            "name": "Bad Key",
            "scopes": ["invalid:scope"],
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_create_key_invalid_expires(self, mock_db_svc, client):
        mock_svc, _ = _mock_db_service()
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.post("/api/v2/api-keys/", json={
            "name": "Key",
            "scopes": [],
            "expires_at": "not-a-date",
        })
        assert resp.status_code == 400


class TestUpdateApiKey:
    """PATCH /api/v2/api-keys/{id}."""

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_update_key_success(self, mock_db_svc, client):
        updated_row = {**MOCK_API_KEY_ROW, "name": "Updated Key"}
        mock_svc, _ = _mock_db_service(fetchrow_return=updated_row)
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.patch("/api/v2/api-keys/1", json={"name": "Updated Key"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Key"

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_update_key_no_fields(self, mock_db_svc, client):
        mock_svc, _ = _mock_db_service()
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.patch("/api/v2/api-keys/1", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_update_key_not_found(self, mock_db_svc, client):
        mock_svc, _ = _mock_db_service(fetchrow_return=None)
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.patch("/api/v2/api-keys/999", json={"name": "X"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_update_key_invalid_scope(self, mock_db_svc, client):
        mock_svc, _ = _mock_db_service()
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.patch("/api/v2/api-keys/1", json={"scopes": ["bad:scope"]})
        assert resp.status_code == 400


class TestDeleteApiKey:
    """DELETE /api/v2/api-keys/{id}."""

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_delete_key_success(self, mock_db_svc, client):
        mock_svc, _ = _mock_db_service(execute_return="DELETE 1")
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.delete("/api/v2/api-keys/1")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_delete_key_not_found(self, mock_db_svc, client):
        mock_svc, _ = _mock_db_service(execute_return="DELETE 0")
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.delete("/api/v2/api-keys/999")
        assert resp.status_code == 404


class TestApiKeysRBAC:
    """RBAC tests for API keys endpoints."""

    @pytest.mark.asyncio
    async def test_anon_unauthorized(self, anon_client):
        resp = await anon_client.get("/api/v2/api-keys/")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_viewer_no_api_keys_access(self, app, viewer):
        app.dependency_overrides[get_current_admin] = lambda: viewer
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v2/api-keys/")
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_viewer_cannot_create_key(self, app, viewer):
        app.dependency_overrides[get_current_admin] = lambda: viewer
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/v2/api-keys/", json={"name": "X", "scopes": []})
            assert resp.status_code == 403
