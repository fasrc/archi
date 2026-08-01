"""
Unit tests for the parse_last_message helper in request_validation.py.

Covers every scenario from
openspec/changes/fix-issue-167-last-message-validation/specs/chat-request-validation/spec.md.
"""

import pytest

from src.interfaces.chat_app.request_validation import (
    InvalidLastMessage,
    parse_last_message,
)

# ---------------------------------------------------------------------------
# Accept cases
# ---------------------------------------------------------------------------


def test_nested_list_pair_accepted():
    """Canonical shape sent by static/chat.js via history.slice(-1)."""
    assert parse_last_message([["User", "How do I submit a job?"]]) == (
        "User",
        "How do I submit a job?",
    )


def test_nested_tuple_pair_accepted():
    """Shape constructed by openai_compat.py: [("user", query)]."""
    assert parse_last_message([("user", "hello")]) == ("user", "hello")


def test_outer_tuple_with_inner_list_accepted():
    """Outer tuple is also a valid container."""
    assert parse_last_message((["User", "hello"],)) == ("User", "hello")


def test_outer_tuple_with_inner_tuple_accepted():
    assert parse_last_message((("user", "hello"),)) == ("user", "hello")


# ---------------------------------------------------------------------------
# Reject cases — each raises InvalidLastMessage
# ---------------------------------------------------------------------------


def test_flat_list_long_sender_raises():
    """Flat pair with a sender longer than 2 chars; today returns HTTP 500."""
    with pytest.raises(InvalidLastMessage):
        parse_last_message(["User", "hello"])


def test_flat_list_two_char_sender_raises():
    """Regression guard for issue #167.

    A flat pair ["AI", "hello"] is a two-item string-sequence, so
    tuple(message[0]) → ("A", "I"), which silently sets sender="A" and
    returns HTTP 200 with wrong content. This test pins the contract that
    it now returns HTTP 400.
    """
    with pytest.raises(InvalidLastMessage):
        parse_last_message(["AI", "hello"])


def test_empty_list_raises():
    with pytest.raises(InvalidLastMessage):
        parse_last_message([])


def test_none_raises():
    with pytest.raises(InvalidLastMessage):
        parse_last_message(None)


def test_one_item_pair_raises():
    with pytest.raises(InvalidLastMessage):
        parse_last_message([["User"]])


def test_three_item_pair_raises():
    with pytest.raises(InvalidLastMessage):
        parse_last_message([["User", "hello", "extra"]])


def test_non_string_message_member_raises():
    with pytest.raises(InvalidLastMessage):
        parse_last_message([["User", 42]])


def test_non_string_sender_member_raises():
    with pytest.raises(InvalidLastMessage):
        parse_last_message([[None, "hello"]])


def test_flat_single_item_raises():
    with pytest.raises(InvalidLastMessage):
        parse_last_message(["AI"])


def test_mapping_as_first_element_raises():
    with pytest.raises(InvalidLastMessage):
        parse_last_message([{"sender": "User"}])


def test_string_value_raises():
    with pytest.raises(InvalidLastMessage):
        parse_last_message("hello")


def test_integer_value_raises():
    with pytest.raises(InvalidLastMessage):
        parse_last_message(42)


# ---------------------------------------------------------------------------
# Error message quality
# ---------------------------------------------------------------------------


def test_error_message_names_expected_shape():
    """The error message must contain a nested example so callers can diagnose."""
    with pytest.raises(InvalidLastMessage, match=r'\[\["User", "hello"\]\]'):
        parse_last_message(["AI", "hello"])
