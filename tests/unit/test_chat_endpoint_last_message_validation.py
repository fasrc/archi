"""
Endpoint-level tests for last_message validation in the two chat handlers.

Drives the unbound FlaskAppWrapper.get_chat_response and
FlaskAppWrapper.get_chat_response_stream with a stub self inside a bare
Flask request context. Confirms that a malformed last_message returns HTTP 400
before the chat pipeline is invoked.
"""

from unittest.mock import MagicMock

import pytest

flask = pytest.importorskip("flask", reason="Flask not installed")
Flask = flask.Flask

from src.interfaces.chat_app.app import FlaskAppWrapper

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PARSE_DEFAULTS = {
    "conversation_id": None,
    "config_name": None,
    "is_refresh": False,
    "client_sent_msg_ts": 0,
    "client_timeout": 0,
    "include_agent_steps": True,
    "include_tool_steps": True,
    "provider": None,
    "model": None,
}


def _make_stub(message, client_id="test-client"):
    """Stub self with _parse_chat_request mocked to return specific values."""
    stub = MagicMock()
    stub._parse_chat_request.return_value = {
        **_PARSE_DEFAULTS,
        "message": message,
        "client_id": client_id,
    }
    return stub


_APP = Flask(__name__)


def _call_non_stream(stub):
    with _APP.test_request_context("/"):
        return FlaskAppWrapper.get_chat_response(stub)


def _call_stream(stub):
    with _APP.test_request_context("/"):
        return FlaskAppWrapper.get_chat_response_stream(stub)


# ---------------------------------------------------------------------------
# Non-streaming endpoint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_message",
    [
        ["AI", "hello"],  # two-char sender — the #167 regression case
        [],  # empty
        None,  # null / omitted
    ],
)
def test_non_stream_malformed_last_message_returns_400(bad_message):
    stub = _make_stub(bad_message)
    result = _call_non_stream(stub)
    response, status = result
    assert status == 400


def test_non_stream_400_body_carries_error_field():
    stub = _make_stub(["AI", "hello"])
    response, status = _call_non_stream(stub)
    body = response.get_json()
    assert "error" in body


def test_non_stream_chat_not_called_on_rejection():
    stub = _make_stub(["AI", "hello"])
    _call_non_stream(stub)
    stub.chat.assert_not_called()


def test_non_stream_chat_not_called_for_empty():
    stub = _make_stub([])
    _call_non_stream(stub)
    stub.chat.assert_not_called()


# ---------------------------------------------------------------------------
# Streaming endpoint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_message",
    [
        ["AI", "hello"],
        [],
        None,
    ],
)
def test_stream_malformed_last_message_returns_400(bad_message):
    stub = _make_stub(bad_message)
    result = _call_stream(stub)
    response, status = result
    assert status == 400


def test_stream_400_body_carries_error_field():
    stub = _make_stub(["AI", "hello"])
    response, status = _call_stream(stub)
    body = response.get_json()
    assert "error" in body


def test_stream_rejection_is_not_a_streaming_response():
    """Rejection must be a plain HTTP 400, not a Response with a generator."""
    stub = _make_stub(["AI", "hello"])
    result = _call_stream(stub)
    response, status = result
    assert status == 400
    # If a streaming Response were returned the second element would be absent,
    # so having a status of 400 and a JSON body proves the generator was not built.
    body = response.get_json()
    assert body is not None


def test_stream_no_meta_line_on_rejection():
    """No opening meta NDJSON line — the generator must never be constructed."""
    stub = _make_stub(["AI", "hello"])
    response, status = _call_stream(stub)
    data = response.get_data(as_text=True)
    assert "meta" not in data
    assert "stream_started" not in data


def test_stream_chat_stream_not_called_on_rejection():
    stub = _make_stub(["AI", "hello"])
    _call_stream(stub)
    stub.chat.stream.assert_not_called()


# ---------------------------------------------------------------------------
# client_id check fires first
# ---------------------------------------------------------------------------


def test_non_stream_missing_client_id_takes_priority():
    """client_id check (existing) fires before last_message check."""
    stub = _make_stub(["AI", "hello"], client_id=None)
    response, status = _call_non_stream(stub)
    body = response.get_json()
    assert status == 400
    assert body.get("error") == "client_id missing"


def test_stream_missing_client_id_takes_priority():
    stub = _make_stub(["AI", "hello"], client_id=None)
    response, status = _call_stream(stub)
    body = response.get_json()
    assert status == 400
    assert body.get("error") == "client_id missing"
