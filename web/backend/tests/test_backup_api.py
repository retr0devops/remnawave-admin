"""Tests for backup API — /api/v2/backups/*."""
import pytest
from datetime import datetime
from unittest.mock import patch, AsyncMock, MagicMock

from web.backend.api.deps import get_current_admin
from .conftest import make_admin


MOCK_BACKUP_FILES = [
    {"filename": "backup_2026-03-01.sql.gz", "size_bytes": 1024000, "created_at": "2026-03-01T10:00:00"},
    {"filename": "backup_2026-03-02.sql.gz", "size_bytes": 2048000, "created_at": "2026-03-02T10:00:00"},
]

MOCK_BACKUP_LOG_ROW = {
    "id": 1,
    "filename": "backup_2026-03-01.sql.gz",
    "backup_type": "database",
    "size_bytes": 1024000,
    "status": "completed",
    "created_by_username": "admin",
    "notes": None,
    "created_at": datetime(2026, 3, 1, 10, 0, 0),
}

MOCK_BACKUP_RESULT = {
    "filename": "backup_2026-03-06.sql.gz",
    "size_bytes": 3072000,
    "backup_type": "database",
}


def _mock_db_service(fetch_return=None, fetchrow_return=None, fetchval_return=None):
    """Create a mock db_service with acquire() context manager."""
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


class TestListBackups:
    """GET /api/v2/backups/."""

    @pytest.mark.asyncio
    @patch("web.backend.core.backup_service.list_backup_files", return_value=MOCK_BACKUP_FILES)
    async def test_list_backups_success(self, mock_list, client):
        resp = await client.get("/api/v2/backups/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["filename"] == "backup_2026-03-01.sql.gz"

    @pytest.mark.asyncio
    @patch("web.backend.core.backup_service.list_backup_files", return_value=[])
    async def test_list_backups_empty(self, mock_list, client):
        resp = await client.get("/api/v2/backups/")
        assert resp.status_code == 200
        assert resp.json() == []


class TestBackupLog:
    """GET /api/v2/backups/log."""

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_backup_log_success(self, mock_db_svc, client):
        mock_svc, mock_conn = _mock_db_service(fetch_return=[MOCK_BACKUP_LOG_ROW])
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.get("/api/v2/backups/log")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["backup_type"] == "database"
        assert data[0]["created_at"] == "2026-03-01T10:00:00"

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    async def test_backup_log_db_disconnected(self, mock_db_svc, client):
        mock_db_svc.is_connected = False
        resp = await client.get("/api/v2/backups/log")
        assert resp.status_code == 200
        assert resp.json() == []


class TestCreateDatabaseBackup:
    """POST /api/v2/backups/database."""

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    @patch("web.backend.core.backup_service.create_database_backup", new_callable=AsyncMock, return_value=MOCK_BACKUP_RESULT)
    @patch.dict("os.environ", {"DATABASE_URL": "postgresql://test:test@localhost/testdb"})
    async def test_create_db_backup_success(self, mock_create, mock_db_svc, client):
        mock_svc, _ = _mock_db_service()
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.post("/api/v2/backups/database")
        assert resp.status_code == 201
        data = resp.json()
        assert data["filename"] == "backup_2026-03-06.sql.gz"
        assert data["backup_type"] == "database"

    @pytest.mark.asyncio
    @patch.dict("os.environ", {}, clear=False)
    async def test_create_db_backup_no_database_url(self, client):
        import os
        old = os.environ.pop("DATABASE_URL", None)
        try:
            resp = await client.post("/api/v2/backups/database")
            assert resp.status_code == 500
        finally:
            if old:
                os.environ["DATABASE_URL"] = old


class TestCreateConfigBackup:
    """POST /api/v2/backups/config."""

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    @patch("web.backend.core.backup_service.export_config", new_callable=AsyncMock, return_value={
        "filename": "config_2026-03-06.json", "size_bytes": 512, "backup_type": "config"
    })
    async def test_create_config_backup_success(self, mock_export, mock_db_svc, client):
        mock_svc, _ = _mock_db_service()
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.post("/api/v2/backups/config")
        assert resp.status_code == 201
        data = resp.json()
        assert data["backup_type"] == "config"


class TestDownloadBackup:
    """GET /api/v2/backups/download/{filename}."""

    @pytest.mark.asyncio
    @patch("web.backend.core.backup_service.get_backup_filepath", return_value=None)
    async def test_download_not_found(self, mock_filepath, client):
        resp = await client.get("/api/v2/backups/download/nonexistent.sql.gz")
        assert resp.status_code == 404


class TestDeleteBackup:
    """DELETE /api/v2/backups/{filename}."""

    @pytest.mark.asyncio
    @patch("web.backend.core.backup_service.delete_backup_file", return_value=True)
    async def test_delete_success(self, mock_delete, client):
        resp = await client.delete("/api/v2/backups/backup_2026-03-01.sql.gz")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    @patch("web.backend.core.backup_service.delete_backup_file", return_value=False)
    async def test_delete_not_found(self, mock_delete, client):
        resp = await client.delete("/api/v2/backups/nonexistent.sql.gz")
        assert resp.status_code == 404


class TestRestoreBackup:
    """POST /api/v2/backups/restore."""

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    @patch("web.backend.core.backup_service.restore_database_backup", new_callable=AsyncMock)
    @patch.dict("os.environ", {"DATABASE_URL": "postgresql://test:test@localhost/testdb"})
    async def test_restore_success(self, mock_restore, mock_db_svc, client):
        mock_svc, _ = _mock_db_service()
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.post("/api/v2/backups/restore", json={"filename": "backup.sql.gz"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    @patch("web.backend.core.backup_service.restore_database_backup", new_callable=AsyncMock, side_effect=FileNotFoundError)
    @patch.dict("os.environ", {"DATABASE_URL": "postgresql://test:test@localhost/testdb"})
    async def test_restore_file_not_found(self, mock_restore, client):
        resp = await client.post("/api/v2/backups/restore", json={"filename": "missing.sql.gz"})
        assert resp.status_code == 404


class TestImportConfig:
    """POST /api/v2/backups/import-config."""

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    @patch("web.backend.core.backup_service.import_config", new_callable=AsyncMock, return_value={"imported_count": 5, "skipped_count": 2})
    async def test_import_config_success(self, mock_import, mock_db_svc, client):
        mock_svc, _ = _mock_db_service()
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.post("/api/v2/backups/import-config", json={"filename": "config.json"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported_count"] == 5
        assert data["skipped_count"] == 2


class TestImportUsers:
    """POST /api/v2/backups/import-users."""

    @pytest.mark.asyncio
    @patch("shared.database.db_service")
    @patch("web.backend.core.backup_service.import_users_from_file", new_callable=AsyncMock, return_value={"imported_count": 10, "skipped_count": 0, "errors": []})
    async def test_import_users_success(self, mock_import, mock_db_svc, client):
        mock_svc, _ = _mock_db_service()
        mock_db_svc.is_connected = mock_svc.is_connected
        mock_db_svc.acquire = mock_svc.acquire

        resp = await client.post("/api/v2/backups/import-users", json={"filename": "users.json"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported_count"] == 10


class TestBackupRBAC:
    """RBAC tests for backup endpoints."""

    @pytest.mark.asyncio
    async def test_anon_unauthorized(self, anon_client):
        resp = await anon_client.get("/api/v2/backups/")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_viewer_no_backup_access(self, app, viewer):
        """Viewer has no backups permissions."""
        app.dependency_overrides[get_current_admin] = lambda: viewer
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v2/backups/")
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_viewer_cannot_create_backup(self, app, viewer):
        app.dependency_overrides[get_current_admin] = lambda: viewer
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/v2/backups/database")
            assert resp.status_code == 403
