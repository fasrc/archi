"""
Unit tests for API token generation, lookup, and revocation in UserService.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.utils.user_service import UserService


@pytest.fixture
def mock_conn():
    """Create a mock database connection with cursor context manager."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cursor


@pytest.fixture
def service(mock_conn):
    """Create a UserService that returns the mock connection."""
    conn, _ = mock_conn
    svc = UserService(pg_config={"host": "localhost"})
    svc._get_connection = MagicMock(return_value=conn)
    svc._release_connection = MagicMock()
    return svc


class TestGenerateApiToken:

    def test_returns_token_with_prefix(self, service):
        _, cursor = service._get_connection().cursor().__enter__(), None
        # Re-setup since fixture access pattern is tricky with context manager
        conn = MagicMock()
        cur = MagicMock()
        cur.rowcount = 1
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        service._get_connection = MagicMock(return_value=conn)

        token = service.generate_api_token("user1")
        assert token.startswith("archi_")

    def test_token_correct_length(self, service):
        conn = MagicMock()
        cur = MagicMock()
        cur.rowcount = 1
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        service._get_connection = MagicMock(return_value=conn)

        token = service.generate_api_token("user1")
        # "archi_" (6 chars) + 32 hex chars = 38 total
        assert len(token) == 38

    def test_stores_sha256_hash(self, service):
        conn = MagicMock()
        cur = MagicMock()
        cur.rowcount = 1
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        service._get_connection = MagicMock(return_value=conn)

        token = service.generate_api_token("user1")
        expected_hash = hashlib.sha256(token.encode()).hexdigest()

        # Verify the hash passed to the DB matches
        call_args = cur.execute.call_args
        sql_params = call_args[0][1]
        assert sql_params[0] == expected_hash
        assert sql_params[1] == "user1"

    def test_raises_if_user_not_found(self, service):
        conn = MagicMock()
        cur = MagicMock()
        cur.rowcount = 0
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        service._get_connection = MagicMock(return_value=conn)

        with pytest.raises(ValueError, match="User not found"):
            service.generate_api_token("nonexistent")


class TestGetUserByApiToken:

    def _make_row(self, api_token_created_at=None):
        return {
            "id": "user1",
            "display_name": "Test User",
            "email": "test@example.com",
            "auth_provider": "basic",
            "is_admin": False,
            "theme": "system",
            "preferred_model": None,
            "preferred_temperature": None,
            "api_token_created_at": api_token_created_at,
            "created_at": "2025-01-01 00:00:00",
            "updated_at": "2025-01-01 00:00:00",
        }

    def test_valid_token_returns_user(self, service):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = self._make_row()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        service._get_connection = MagicMock(return_value=conn)

        token = "archi_abc123"
        user = service.get_user_by_api_token(token)

        assert user is not None
        assert user.id == "user1"

        # Verify the hash was queried
        expected_hash = hashlib.sha256(token.encode()).hexdigest()
        call_args = cur.execute.call_args[0][1]
        assert call_args[0] == expected_hash

    def test_invalid_token_returns_none(self, service):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = None
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        service._get_connection = MagicMock(return_value=conn)

        user = service.get_user_by_api_token("archi_invalid")
        assert user is None


class TestTokenExpiry:

    def _make_row(self, api_token_created_at=None):
        return {
            "id": "user1",
            "display_name": "Test User",
            "email": "test@example.com",
            "auth_provider": "basic",
            "is_admin": False,
            "theme": "system",
            "preferred_model": None,
            "preferred_temperature": None,
            "api_token_created_at": api_token_created_at,
            "created_at": "2025-01-01 00:00:00",
            "updated_at": "2025-01-01 00:00:00",
        }

    def test_recent_token_returns_user(self, service):
        """Token created within TTL returns user (Task 3.3)."""
        conn = MagicMock()
        cur = MagicMock()
        recent = datetime.now(timezone.utc) - timedelta(days=10)
        cur.fetchone.return_value = self._make_row(api_token_created_at=recent)
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        service._get_connection = MagicMock(return_value=conn)

        user = service.get_user_by_api_token("archi_abc123", token_ttl_days=90)
        assert user is not None
        assert user.id == "user1"

    def test_expired_token_returns_none(self, service):
        """Token older than TTL returns None (Task 3.4)."""
        conn = MagicMock()
        cur = MagicMock()
        old = datetime.now(timezone.utc) - timedelta(days=100)
        cur.fetchone.return_value = self._make_row(api_token_created_at=old)
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        service._get_connection = MagicMock(return_value=conn)

        user = service.get_user_by_api_token("archi_abc123", token_ttl_days=90)
        assert user is None

    def test_null_created_at_returns_user(self, service):
        """Token with NULL api_token_created_at returns user for backward compat (Task 3.5)."""
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = self._make_row(api_token_created_at=None)
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        service._get_connection = MagicMock(return_value=conn)

        user = service.get_user_by_api_token("archi_abc123", token_ttl_days=90)
        assert user is not None
        assert user.id == "user1"


class TestRevokeApiToken:

    def test_revoke_existing_token(self, service):
        conn = MagicMock()
        cur = MagicMock()
        cur.rowcount = 1
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        service._get_connection = MagicMock(return_value=conn)

        result = service.revoke_api_token("user1")
        assert result is True

    @patch("src.utils.user_service.log_authentication_event")
    def test_revoke_logs_audit_event(self, mock_log, service):
        conn = MagicMock()
        cur = MagicMock()
        cur.rowcount = 1
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        service._get_connection = MagicMock(return_value=conn)

        service.revoke_api_token("user1")
        mock_log.assert_called_once_with("user1", "api_token_revoke", success=True, method="bearer_token")

    def test_revoke_no_token_returns_false(self, service):
        conn = MagicMock()
        cur = MagicMock()
        cur.rowcount = 0
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        service._get_connection = MagicMock(return_value=conn)

        result = service.revoke_api_token("user_no_token")
        assert result is False


class TestTokenRegeneration:

    def test_new_token_replaces_old(self, service):
        conn = MagicMock()
        cur = MagicMock()
        cur.rowcount = 1
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        service._get_connection = MagicMock(return_value=conn)

        token1 = service.generate_api_token("user1")
        token2 = service.generate_api_token("user1")

        assert token1 != token2
        assert token1.startswith("archi_")
        assert token2.startswith("archi_")
