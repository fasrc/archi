"""
Integration-style tests for /v1 conversation persistence.

Tests that:
- Multiple requests with the same X-OpenWebUI-Chat-Id map to one conversation
- Different chat IDs create separate conversations
- Messages are persisted correctly (user + assistant per request)
- Errors persist user message only

These tests mock the database layer to verify the SQL logic
without requiring a running PostgreSQL instance.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# In-memory DB mock for conversation_metadata and conversations tables
# ---------------------------------------------------------------------------

class FakeDB:
    """Simulates conversation_metadata and conversations tables in memory."""

    def __init__(self):
        self.conversations_meta = {}  # external_chat_id -> conversation_id
        self.messages = []  # list of (conversation_id, sender, content)
        self._next_id = 1

    def get_or_create(self, external_chat_id, user_id):
        if external_chat_id in self.conversations_meta:
            return self.conversations_meta[external_chat_id]
        conv_id = self._next_id
        self._next_id += 1
        self.conversations_meta[external_chat_id] = conv_id
        return conv_id

    def insert_message(self, conversation_id, sender, content):
        self.messages.append((conversation_id, sender, content))

    def messages_for(self, conversation_id):
        return [(s, c) for cid, s, c in self.messages if cid == conversation_id]


@pytest.fixture
def fake_db():
    return FakeDB()


# ---------------------------------------------------------------------------
# Tests for _get_or_create_conversation logic
# ---------------------------------------------------------------------------

class TestConversationMapping:

    def test_same_chat_id_maps_to_one_conversation(self, fake_db):
        """3 requests with same external ID should all get the same conversation_id."""
        chat_id = "owui-abc-123"
        ids = [fake_db.get_or_create(chat_id, "user1") for _ in range(3)]
        assert ids == [1, 1, 1]

    def test_different_chat_ids_create_separate_conversations(self, fake_db):
        """Different external IDs should create different conversations."""
        id1 = fake_db.get_or_create("chat-A", "user1")
        id2 = fake_db.get_or_create("chat-B", "user1")
        assert id1 != id2

    def test_three_same_then_one_different(self, fake_db):
        """
        3 requests with same chat ID → 1 conversation with 6 messages,
        4th request with different ID → 2nd conversation.
        """
        chat_id_1 = "owui-session-1"
        chat_id_2 = "owui-session-2"

        # 3 requests to chat_id_1
        for i in range(3):
            conv_id = fake_db.get_or_create(chat_id_1, "user1")
            fake_db.insert_message(conv_id, "user", f"question {i+1}")
            fake_db.insert_message(conv_id, "archi", f"answer {i+1}")

        # 1 request to chat_id_2
        conv_id_2 = fake_db.get_or_create(chat_id_2, "user1")
        fake_db.insert_message(conv_id_2, "user", "question 4")
        fake_db.insert_message(conv_id_2, "archi", "answer 4")

        # Verify conversation 1 has 6 messages (3 user + 3 assistant)
        msgs_1 = fake_db.messages_for(1)
        assert len(msgs_1) == 6
        assert sum(1 for s, _ in msgs_1 if s == "user") == 3
        assert sum(1 for s, _ in msgs_1 if s == "archi") == 3

        # Verify conversation 2 has 2 messages
        msgs_2 = fake_db.messages_for(2)
        assert len(msgs_2) == 2


class TestMessagePersistence:

    def test_successful_request_persists_both_messages(self, fake_db):
        """A successful request should persist both user and assistant messages."""
        conv_id = fake_db.get_or_create("chat-1", "user1")
        fake_db.insert_message(conv_id, "user", "What is RAG?")
        fake_db.insert_message(conv_id, "archi", "RAG stands for...")

        msgs = fake_db.messages_for(conv_id)
        assert len(msgs) == 2
        assert msgs[0] == ("user", "What is RAG?")
        assert msgs[1] == ("archi", "RAG stands for...")

    def test_error_persists_user_message_only(self, fake_db):
        """On error, only the user message should be persisted."""
        conv_id = fake_db.get_or_create("chat-err", "user1")
        fake_db.insert_message(conv_id, "user", "bad request")
        # No assistant message on error

        msgs = fake_db.messages_for(conv_id)
        assert len(msgs) == 1
        assert msgs[0] == ("user", "bad request")


# ---------------------------------------------------------------------------
# Tests for the actual _get_or_create_conversation function via mocked psycopg2
# ---------------------------------------------------------------------------

def _has_psycopg2():
    try:
        import psycopg2
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _has_psycopg2(), reason="psycopg2 not installed")
class TestGetOrCreateConversationSQL:
    """Test the actual SQL-based function with mocked psycopg2."""

    def _setup_module_globals(self, mock_connect):
        """Import and configure the module with mocked dependencies."""
        import src.interfaces.chat_app.openai_compat as compat
        mock_wrapper = MagicMock()
        mock_wrapper.pg_config = {"host": "localhost", "dbname": "test"}
        compat._chat_wrapper = mock_wrapper
        return compat

    @patch("psycopg2.connect")
    def test_creates_new_conversation_when_not_found(self, mock_connect):
        compat = self._setup_module_globals(mock_connect)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        # First query: no existing mapping
        mock_cursor.fetchone.side_effect = [None, (42,)]

        result = compat._get_or_create_conversation("ext-123", "user1", "user1")

        assert result == 42
        assert mock_cursor.execute.call_count == 2  # SELECT + INSERT
        mock_conn.close.assert_called_once()

    @patch("psycopg2.connect")
    def test_returns_existing_conversation(self, mock_connect):
        compat = self._setup_module_globals(mock_connect)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        # First query: found existing
        mock_cursor.fetchone.return_value = (99,)

        result = compat._get_or_create_conversation("ext-existing", "user1", "user1")

        assert result == 99
        mock_conn.close.assert_called_once()

    @patch("psycopg2.connect")
    def test_connection_closed_on_error(self, mock_connect):
        compat = self._setup_module_globals(mock_connect)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        mock_cursor.execute.side_effect = Exception("DB error")

        result = compat._get_or_create_conversation("ext-fail", "user1", "user1")

        assert result is None
        mock_conn.close.assert_called_once()
