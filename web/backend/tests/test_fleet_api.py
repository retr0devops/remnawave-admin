"""Tests for fleet API — /api/v2/fleet/*."""
import pytest
from datetime import datetime
from unittest.mock import patch, AsyncMock, MagicMock

from web.backend.api.deps import get_current_admin
from .conftest import make_admin


MOCK_NODE_ROW = {
    "uuid": "node-111",
    "name": "Frankfurt-1",
    "address": "10.0.0.1",
    "agent_v2_connected": True,
    "agent_v2_last_ping": datetime(2026, 3, 6, 10, 30, 0),
}

MOCK_NODE_ROW_OFFLINE = {
    "uuid": "node-222",
    "name": "London-1",
    "address": "10.0.0.2",
    "agent_v2_connected": False,
    "agent_v2_last_ping": None,
}

MOCK_COMMAND_LOG_ROW = {
    "id": 1,
    "node_uuid": "node-111",
    "admin_username": "admin",
    "command_type": "exec_script",
    "command_data": "script=check_disk",
    "status": "completed",
    "output": "OK",
    "exit_code": 0,
    "started_at": datetime(2026, 3, 6, 10, 0, 0),
    "finished_at": datetime(2026, 3, 6, 10, 0, 5),
    "duration_ms": 5000,
}


def _mock_db_service(fetch_return=None, fetchrow_return=None, fetchval_return=None):
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=fetch_return or [])
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    mock_conn.fetchval = AsyncMock(return_value=fetchval_return)
    mock_conn.execute = AsyncMock()

    mock_svc = MagicMock()
    mock_svc.is_connected = True
    mock_svc.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_svc.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_svc, mock_conn


class TestFleetAgents:
    """GET /api/v2/fleet/agents."""

    @pytest.mark.asyncio
    @patch("web.backend.api.v2.fleet.agent_manager")
    @patch("shared.database.db_service")
    async def test_agents_list_success(self, mock_db_svc, mock_agent_mgr, client):
        mock_svc, _ = _mock_db_service(fetch_return=[MOCK_NODE_ROW, MOCK_NODE_ROW_OFFLINE])
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        # node-111 is live, node-222 is not
        mock_agent_mgr.is_connected = MagicMock(side_effect=lambda uuid: uuid == "node-111")

        resp = await client.get("/api/v2/fleet/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 2
        assert data["connected_count"] == 1
        assert len(data["nodes"]) == 2

        # Verify node-111 is connected
        node_111 = next(n for n in data["nodes"] if n["uuid"] == "node-111")
        assert node_111["agent_v2_connected"] is True
        assert node_111["name"] == "Frankfurt-1"

        # node-222 is offline
        node_222 = next(n for n in data["nodes"] if n["uuid"] == "node-222")
        assert node_222["agent_v2_connected"] is False

    @pytest.mark.asyncio
    @patch("web.backend.api.v2.fleet.agent_manager")
    @patch("shared.database.db_service")
    async def test_agents_db_says_connected_but_live_says_no(self, mock_db_svc, mock_agent_mgr, client):
        """Cross-check: DB says connected but agent_manager disagrees."""
        mock_svc, _ = _mock_db_service(fetch_return=[MOCK_NODE_ROW])
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        mock_agent_mgr.is_connected = MagicMock(return_value=False)

        resp = await client.get("/api/v2/fleet/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected_count"] == 0
        assert data["nodes"][0]["agent_v2_connected"] is False

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_agents_db_disconnected(self, mock_db_svc, client):
        mock_db_svc.is_connected = False
        resp = await client.get("/api/v2/fleet/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 0
        assert data["nodes"] == []


class TestCommandLog:
    """GET /api/v2/fleet/command-log."""

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_command_log_success(self, mock_db_svc, client):
        mock_svc, mock_conn = _mock_db_service(
            fetch_return=[MOCK_COMMAND_LOG_ROW],
            fetchval_return=1,
        )
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.get("/api/v2/fleet/command-log")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["entries"]) == 1
        assert data["entries"][0]["command_type"] == "exec_script"
        assert data["entries"][0]["exit_code"] == 0

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_command_log_with_filters(self, mock_db_svc, client):
        mock_svc, mock_conn = _mock_db_service(fetch_return=[], fetchval_return=0)
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.get("/api/v2/fleet/command-log?node_uuid=node-111&command_type=exec_script&page=1&per_page=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["page"] == 1
        assert data["per_page"] == 10

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_command_log_db_disconnected(self, mock_db_svc, client):
        mock_db_svc.is_connected = False
        resp = await client.get("/api/v2/fleet/command-log")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


class TestFleetRBAC:
    """RBAC tests for fleet endpoints."""

    @pytest.mark.asyncio
    async def test_anon_unauthorized(self, anon_client):
        resp = await anon_client.get("/api/v2/fleet/agents")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    @patch("web.backend.api.v2.fleet.agent_manager")
    @patch("shared.database.db_service")
    async def test_viewer_can_view_agents(self, mock_db_svc, mock_agent_mgr, app, viewer):
        """Viewer has fleet.view permission."""
        mock_svc, _ = _mock_db_service(fetch_return=[])
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        app.dependency_overrides[get_current_admin] = lambda: viewer
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v2/fleet/agents")
            assert resp.status_code == 200
