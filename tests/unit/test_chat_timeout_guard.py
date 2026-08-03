"""Timeout guard in ``_prepare_chat_context`` (issue #175).

A falsey ``client_timeout`` or ``client_sent_msg_ts`` means the caller did not declare a
client-side deadline, so the server must not apply one.  The guard at ``app.py:1715``
must be conditional on *both* fields being truthy before comparing elapsed time against
the timeout value.

The explicit-deadline test (a non-zero pair where the window is genuinely exceeded) must
**pass both before and after the fix** — its purpose is to prove the guard was tightened,
not removed.  If the test were deleted rather than corrected it would give a false green on
a completely absent check.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import src.interfaces.chat_app.app as app_module
from src.interfaces.chat_app.app import ChatWrapper

CLIENT_ID = "client-1"
INCOMING = [["User", "hello"]]


class _FakeOutput:
    """A pipeline output implementing the two members ``stream`` reads off it.

    ``.get`` matters: finalization calls ``last_output.get("source_documents", [])``, so an
    output without it aborts the stream with an in-band 500 instead of a ``final`` event.
    """

    def __init__(self, answer):
        self.answer = answer
        self.content = answer
        self.metadata = {"event_type": "text"}

    def get(self, key, default=None):
        return default


def _wrapper(created=None, stored_history=None, touched=None):
    """A ChatWrapper carrying only the collaborators ``_prepare_chat_context`` touches.

    ``created`` records conversation creations and ``touched`` timestamp updates so a
    test can assert that a refused request writes nothing.
    """
    if created is None:
        created = []

    wrapper = object.__new__(ChatWrapper)

    def create_conversation(first_message, client_id, user_id=None):
        created.append(first_message)
        return 42

    def update_conversation_timestamp(conversation_id, client_id, user_id=None):
        if touched is not None:
            touched.append(conversation_id)

    wrapper.create_conversation = create_conversation
    wrapper.query_conversation_history = (
        lambda conversation_id, client_id, user_id=None: list(stored_history or [])
    )
    wrapper.update_conversation_timestamp = update_conversation_timestamp
    return wrapper


class TestMissingTimingFieldsDoNotTrigger408:
    """A falsey client_sent_msg_ts or client_timeout means no declared deadline."""

    def test_both_absent_does_not_return_408(self):
        # _parse_chat_request coerces absent fields to 0; the guard must treat 0 as
        # "no deadline", not as a baseline of the Unix epoch.
        context, error_code = _wrapper()._prepare_chat_context(
            INCOMING,
            None,
            CLIENT_ID,
            False,
            datetime.now(timezone.utc),
            0,  # client_sent_msg_ts absent → coerced to 0
            0,  # client_timeout absent → coerced to 0
            {},
        )

        assert error_code != 408
        assert context is not None

    def test_timeout_only_does_not_return_408(self):
        # A timeout supplied without a send timestamp would measure elapsed time from the
        # Unix epoch, which exceeds any finite timeout — so the guard must be disabled.
        context, error_code = _wrapper()._prepare_chat_context(
            INCOMING,
            None,
            CLIENT_ID,
            False,
            datetime.now(timezone.utc),
            0,  # client_sent_msg_ts absent → coerced to 0
            600.0,  # non-zero client_timeout
            {},
        )

        assert error_code != 408
        assert context is not None

    def test_timestamp_only_does_not_return_408(self):
        # A send timestamp without a timeout means no declared deadline; 0 timeout must
        # not be interpreted as "deadline of zero seconds".  Use a past send time so
        # elapsed >> 0 — the unguarded comparison would return 408, proving the guard
        # actually fired rather than a coincidental zero elapsed time.
        context, error_code = _wrapper()._prepare_chat_context(
            INCOMING,
            None,
            CLIENT_ID,
            False,
            datetime.now(timezone.utc),
            1.0,  # client_sent_msg_ts: 1 second after epoch, elapsed >> 0
            0,  # client_timeout absent → coerced to 0
            {},
        )

        assert error_code != 408
        assert context is not None


class TestExplicitDeadlineIsStillEnforced:
    """Making the fields optional must not remove the check for a declared deadline."""

    def test_exceeded_deadline_returns_408(self):
        # sent=1ms ago, timeout=0ms → window is genuinely exceeded.
        # This test must pass both before and after the fix.
        context, error_code = _wrapper()._prepare_chat_context(
            INCOMING,
            None,
            CLIENT_ID,
            False,
            datetime.now(timezone.utc),
            1.0,  # client_sent_msg_ts: 1 second after epoch, guaranteed exceeded
            0.001,  # client_timeout: 1 ms — any real server latency exceeds this
            {},
        )

        assert error_code == 408
        assert context is None

    def test_deadline_not_yet_reached_is_accepted(self):
        # sent=now, generous timeout → deadline is in the future.
        now = datetime.now(timezone.utc)
        context, error_code = _wrapper()._prepare_chat_context(
            INCOMING,
            None,
            CLIENT_ID,
            False,
            now,
            now.timestamp(),
            600.0,  # 10-minute deadline
            {},
        )

        assert error_code is None
        assert context is not None


class TestTheInStreamCheckNeedsOnlyTheTimeout:
    """The in-stream guard reads ``client_timeout`` alone, and that is deliberate.

    ``_prepare_chat_context``'s guard needs ``client_sent_msg_ts`` because it measures from
    the client's send time — with no baseline it would measure from the epoch and reject
    everything.  The in-stream guard measures from ``stream_start_time``, a *server-side*
    baseline, so a supplied ``client_timeout`` is enforceable there even when no timestamp
    came with it.

    Making the two guards identical — the obvious-looking "consistency" fix — would discard
    a deadline the caller explicitly declared.  This is the test that fails if someone tries,
    which is why it exists rather than a comment saying so.
    """

    def _streaming_wrapper(self):
        """A ChatWrapper stubbed to what ``stream`` touches, per test_chat_override_persistence.

        ``cursor``/``conn`` are None so the ``finally`` block that closes them is a no-op,
        and ``_finalize_result`` is stubbed so the no-timeout case can run to completion
        without a database.

        The yielded output must implement the production interface -- ``stream`` calls
        ``.get("source_documents", …)`` on the last output during finalization. A bare
        ``SimpleNamespace`` has no ``.get``, which ends the stream with an in-band 500 and
        makes any assertion phrased as "no 408" pass while the stream is in fact crashing.
        """
        wrapper = _wrapper()
        wrapper.archi = SimpleNamespace(
            stream=lambda **kwargs: iter([_FakeOutput("x")]),
            pipeline_name="test-pipeline",
        )
        wrapper._resolve_config_name = lambda config_name: config_name or "default"
        wrapper.update_config = lambda config_name=None: None
        wrapper.current_model_used = "test-model"
        wrapper.number_of_queries = 0
        wrapper.cursor = None
        wrapper.conn = None
        # A None trace_id short-circuits the trace update inside the timeout branch.
        wrapper.create_agent_trace = lambda **kwargs: None
        wrapper.update_agent_trace = lambda **kwargs: None
        wrapper.insert_timing = lambda *a, **k: None
        wrapper._finalize_result = lambda result, **kwargs: ("out", [])
        return wrapper

    def _run(self, monkeypatch, client_sent_msg_ts, client_timeout):
        # First reading is stream_start_time; every later one is far past any deadline.
        readings = iter([0.0] + [1_000_000.0] * 8)
        monkeypatch.setattr(
            app_module, "time", SimpleNamespace(time=lambda: next(readings))
        )

        return list(
            self._streaming_wrapper().stream(
                INCOMING,
                None,
                CLIENT_ID,
                False,
                datetime.now(timezone.utc),
                client_sent_msg_ts,
                client_timeout,
                "default",
            )
        )

    def test_timeout_without_a_timestamp_still_ends_the_stream_with_408(
        self, monkeypatch
    ):
        events = self._run(monkeypatch, 0, 600.0)

        assert events[-1]["type"] == "error"
        assert events[-1]["status"] == 408

    def test_no_timeout_at_all_lets_the_stream_run(self, monkeypatch):
        """The other half of the rule: a falsey timeout means no deadline, as before.

        Asserting on the *absence* of a 408 is not enough — a stream that dies of anything
        else also has no 408 in it, so the test would pass while the thing it is named for
        never happened. It has to reach a ``final`` event.
        """
        events = self._run(monkeypatch, 0, 0)

        assert [event.get("type") for event in events] == ["chunk", "final"]
        assert not any(event.get("type") == "error" for event in events)
