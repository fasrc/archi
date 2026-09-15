"""Route-level handling of the optional timing fields (issue #175).

Unlike ``test_chat_timeout_guard.py``, which drives ``_prepare_chat_context`` directly,
these tests run the **real** ``_parse_chat_request`` against a real JSON request body and
then the real handler, stubbing only the pipeline and the database.  That covers the two
seams a stubbed-method test cannot reach: JSON decoding of an omitted key, and the handler
wiring that carries the parsed value through to ``insert_timing``.

Two behaviours are pinned here:

1. **Omission survives the round trip.** A body with no ``client_sent_msg_ts`` and no
   ``client_timeout`` reaches the pipeline as ``0``/``0`` and still writes a timing row.
2. **An unrepresentable timestamp is refused with 400, before any work.** Making the
   deadline check conditional on both fields means a supplied-but-absurd
   ``client_sent_msg_ts`` (e.g. ``-1e20``) with no ``client_timeout`` no longer hits the
   old unconditional 408.  Left unvalidated it would run the whole pipeline and then raise
   from ``datetime.fromtimestamp`` at persistence time -- ``OSError`` on this platform,
   ``OverflowError`` or ``ValueError`` on others -- turning a bad request into a 500 after
   paying for generation.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

flask = pytest.importorskip("flask", reason="Flask not installed")

from src.interfaces.chat_app.app import FlaskAppWrapper
from src.interfaces.chat_app.request_validation import (
    InvalidClientTiming,
    parse_client_sent_msg_ts,
    parse_client_timeout,
)

_APP = flask.Flask(__name__)

GOOD_BODY = {
    "last_message": [["User", "hello"]],
    "client_id": "web-test",
}


def _stub():
    """A stub self whose ``_parse_chat_request`` is the production method.

    ``current_model_used`` is a real string because the handler puts it straight into the
    JSON body, and a ``MagicMock`` there fails serialization rather than the assertion.
    """
    stub = MagicMock()
    stub._parse_chat_request = lambda: FlaskAppWrapper._parse_chat_request(stub)
    stub.chat.return_value = ("answer", 7, [11], {}, None)
    stub.chat.current_model_used = "test/model"
    return stub


def _post(handler, body):
    stub = _stub()
    with _APP.test_request_context("/", json=body):
        return stub, handler(stub)


class TestOmittedTimingFieldsSurviveTheRoute:
    def test_the_non_streaming_route_answers_and_writes_a_timing_row(self):
        stub, response = _post(FlaskAppWrapper.get_chat_response, GOOD_BODY)

        # Not a 4xx/5xx tuple: the handler returned a single response object.
        assert not isinstance(response, tuple), response

        # The parsed absence reached the pipeline as 0/0 ...
        args = stub.chat.call_args.args
        assert args[5] == 0 and args[6] == 0

        # ... and the row was still written, with the epoch sentinel in place.
        stub.chat.insert_timing.assert_called_once()
        message_id, timestamps = stub.chat.insert_timing.call_args.args
        assert message_id == 11
        assert timestamps["client_sent_msg_ts"] == datetime(
            1970, 1, 1, tzinfo=timezone.utc
        )
        assert timestamps["server_response_msg_ts"] > timestamps["client_sent_msg_ts"]


class TestAnUnrepresentableTimestampIsRefusedNotCrashed:
    # -1e20 ms is the value from the round-4 review; the year-0 and year-10000 edges
    # raise a different exception type, so all three are covered by one check.
    BAD = [-100000000000000000000, -62135596801000, 253402300800000, 10**30]

    @pytest.mark.parametrize("bad_ts", BAD)
    def test_the_non_streaming_route_returns_400(self, bad_ts):
        stub, response = _post(
            FlaskAppWrapper.get_chat_response,
            {**GOOD_BODY, "client_sent_msg_ts": bad_ts},
        )

        assert isinstance(response, tuple) and response[1] == 400
        assert "client_sent_msg_ts" in response[0].get_json()["error"]
        # Refused before the pipeline ran, so no generation was paid for.
        stub.chat.assert_not_called()

    @pytest.mark.parametrize("bad_ts", BAD)
    def test_the_streaming_route_returns_400(self, bad_ts):
        stub, response = _post(
            FlaskAppWrapper.get_chat_response_stream,
            {**GOOD_BODY, "client_sent_msg_ts": bad_ts},
        )

        assert isinstance(response, tuple) and response[1] == 400
        stub.chat.stream.assert_not_called()

    def test_a_representable_timestamp_is_still_accepted(self):
        """The discriminator: the guard must reject only what cannot be converted."""
        stub, response = _post(
            FlaskAppWrapper.get_chat_response,
            {**GOOD_BODY, "client_sent_msg_ts": 1_700_000_000_000},
        )

        assert not isinstance(response, tuple), response
        assert stub.chat.call_args.args[5] == 1_700_000_000.0


class TestNormalizationItselfCannotRaise:
    """The millisecond→second division is part of the untrusted-input surface.

    ``client_sent_msg_ts / 1000`` raises before any range check can run: ``OverflowError``
    for an integer too large to become a float (a 1001-digit JSON integer is valid JSON),
    and ``TypeError`` for a non-numeric value such as a quoted number. Both fields are
    divided, so both are exposed. Left unguarded these are 500s on a well-formed request
    body, and on the streaming route a 500 the caller sees instead of the documented 400.
    """

    HUGE = int("9" * 1001)

    CASES = [
        ("client_sent_msg_ts", HUGE),
        ("client_timeout", HUGE),
        ("client_sent_msg_ts", "1700000000000"),
        ("client_timeout", "600000"),
        ("client_sent_msg_ts", [1]),
        # Booleans: bool is an int subclass, so both divide without raising and slip
        # past the OverflowError/TypeError guard that catches every other non-numeric type.
        # False is listed separately from True because it is falsey — a bool check placed
        # after the `if not value` guard cannot see it, leaving `false` silently accepted
        # as "field omitted" while `true` is refused.  Deleting the False cases removes
        # the only test that pins this ordering constraint.
        ("client_sent_msg_ts", True),
        ("client_sent_msg_ts", False),
        ("client_timeout", True),
        ("client_timeout", False),
        # Non-finite floats for client_timeout: unguarded before this fix — inf / 1000 is
        # still inf, and NaN disables the deadline silently because every comparison with
        # NaN evaluates False.
        ("client_timeout", float("inf")),
        ("client_timeout", float("-inf")),
        ("client_timeout", float("nan")),
        # Non-finite floats for client_sent_msg_ts: already refused today via
        # datetime.fromtimestamp, but pinned here so a regression in _milliseconds_to_seconds
        # cannot hide behind the downstream representable-time check.
        ("client_sent_msg_ts", float("inf")),
        ("client_sent_msg_ts", float("-inf")),
        ("client_sent_msg_ts", float("nan")),
    ]

    @pytest.mark.parametrize("field,value", CASES)
    def test_the_non_streaming_route_returns_400(self, field, value):
        stub, response = _post(
            FlaskAppWrapper.get_chat_response, {**GOOD_BODY, field: value}
        )

        assert isinstance(response, tuple) and response[1] == 400
        assert field in response[0].get_json()["error"]
        stub.chat.assert_not_called()

    @pytest.mark.parametrize("field,value", CASES)
    def test_the_streaming_route_returns_400(self, field, value):
        stub, response = _post(
            FlaskAppWrapper.get_chat_response_stream, {**GOOD_BODY, field: value}
        )

        assert isinstance(response, tuple) and response[1] == 400
        stub.chat.stream.assert_not_called()

    def test_a_normal_timeout_still_reaches_the_pipeline_in_seconds(self):
        stub, response = _post(
            FlaskAppWrapper.get_chat_response, {**GOOD_BODY, "client_timeout": 600_000}
        )

        assert not isinstance(response, tuple), response
        assert stub.chat.call_args.args[6] == 600.0

    @pytest.mark.parametrize(
        "fn,value",
        [
            (parse_client_timeout, True),
            (parse_client_timeout, False),
            (parse_client_sent_msg_ts, True),
            (parse_client_sent_msg_ts, False),
        ],
    )
    def test_direct_call_raises_for_boolean(self, fn, value):
        with pytest.raises(InvalidClientTiming):
            fn(value)
