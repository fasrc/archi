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

import time
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


class TestProviderStallDetection:
    """Executor-based deadline ends a provider that stalls before the first yield.

    Wall-clock enforcement is the thing under test here — the clock is NOT monkeypatched.
    The generator sleeps longer than the declared deadline; the stream must produce a 408
    well before the sleep completes.
    """

    _TIMEOUT = 0.2  # declared client deadline (200 ms)
    _SLEEP = 0.5  # generator stalls for 500 ms before yielding

    def _stalling_wrapper(self):
        sleep = self._SLEEP

        def stalling_stream(**kwargs):
            time.sleep(sleep)
            yield _FakeOutput("x")

        wrapper = _wrapper()
        wrapper.archi = SimpleNamespace(
            stream=stalling_stream,
            pipeline_name="test-pipeline",
        )
        wrapper._resolve_config_name = lambda config_name: config_name or "default"
        wrapper.update_config = lambda config_name=None: None
        wrapper.current_model_used = "test-model"
        wrapper.number_of_queries = 0
        wrapper.cursor = None
        wrapper.conn = None
        wrapper.create_agent_trace = lambda **kwargs: None
        wrapper.update_agent_trace = lambda **kwargs: None
        wrapper.insert_timing = lambda *a, **k: None
        wrapper._finalize_result = lambda result, **kwargs: ("out", [])
        return wrapper

    def test_stall_before_first_yield_produces_408(self):
        start = time.monotonic()
        events = list(
            self._stalling_wrapper().stream(
                INCOMING,
                None,
                CLIENT_ID,
                False,
                datetime.now(timezone.utc),
                0,
                self._TIMEOUT,
                "default",
            )
        )
        elapsed = time.monotonic() - start

        assert events[-1]["type"] == "error"
        assert events[-1]["status"] == 408
        # Must complete well before the sleep completes — executor fires at the deadline.
        assert elapsed < self._SLEEP, (
            f"stream took {elapsed:.3f}s but generator sleeps {self._SLEEP}s — "
            "the stall deadline did not fire"
        )

    def test_no_deadline_means_direct_iteration_no_executor(self):
        """A falsey client_timeout must not create a worker thread.

        The generator yields normally; the stream reaches a ``final`` event.
        No 408 is produced.
        """
        events = list(
            self._stalling_wrapper().stream(
                INCOMING,
                None,
                CLIENT_ID,
                False,
                datetime.now(timezone.utc),
                0,
                0,  # falsey — no deadline
                "default",
            )
        )

        assert not any(e.get("type") == "error" for e in events)


class TestContextPropagation:
    """The executor advance must run inside a snapshot of the caller's contextvars.

    Python 3.12 ThreadPoolExecutor workers start with an empty context, so without
    explicit ctx.run two failure modes are silent and fail open:
    - tools/base.py:36-42: has_request_context() False → RBAC allows every tool
    - prompt_utils.py:14-18: no request context → roles dropped from prompt
    Each test asserts the positive (the worker sees the expected value) rather than
    the absence of a crash, which would prove nothing.
    """

    _TIMEOUT = 5.0  # generous; tests are about context, not timing

    def _context_wrapper(self, gen_fn):
        wrapper = _wrapper()
        wrapper.archi = SimpleNamespace(
            stream=gen_fn,
            pipeline_name="test-pipeline",
        )
        wrapper._resolve_config_name = lambda config_name: config_name or "default"
        wrapper.update_config = lambda config_name=None: None
        wrapper.current_model_used = "test-model"
        wrapper.number_of_queries = 0
        wrapper.cursor = None
        wrapper.conn = None
        wrapper.create_agent_trace = lambda **kwargs: None
        wrapper.update_agent_trace = lambda **kwargs: None
        wrapper.insert_timing = lambda *a, **k: None
        wrapper._finalize_result = lambda result, **kwargs: ("out", [])
        return wrapper

    def test_request_context_visible_in_worker(self):
        """has_request_context() must be True inside the generator on every advance.

        Without ctx.run the worker starts with an empty context, so
        has_request_context() returns False and the RBAC gate allows all tools.
        """
        from flask import Flask, has_request_context

        seen = []

        def gen(**kwargs):
            seen.append(has_request_context())
            yield _FakeOutput("x")
            seen.append(has_request_context())
            yield _FakeOutput("y")

        flask_app = Flask(__name__)
        with flask_app.test_request_context("/"):
            list(
                self._context_wrapper(gen).stream(
                    INCOMING,
                    None,
                    CLIENT_ID,
                    False,
                    datetime.now(timezone.utc),
                    0,
                    self._TIMEOUT,
                    "default",
                )
            )

        assert seen == [True, True], (
            f"Generator saw has_request_context()={seen!r} — "
            "ctx.run() is not propagating the Flask request context to the worker"
        )

    def test_contextvar_mutation_persists_across_advances(self):
        """A ContextVar written on advance 1 must be readable on advance 2.

        Without ctx.run the worker sees an empty context, so a value set in the
        caller is invisible on advance 1.  With one reused ctx snapshot, advance 1
        sees the caller's value and advance 2 sees advance 1's mutation — proving
        that mutations are not discarded between advances.
        """
        import contextvars

        _VAR: contextvars.ContextVar[str] = contextvars.ContextVar(
            "_ctx_prop_test", default="unset"
        )
        _VAR.set("set-in-caller")
        seen = []

        def gen(**kwargs):
            seen.append(_VAR.get())  # advance 1: must see "set-in-caller"
            _VAR.set("set-by-advance-1")
            yield _FakeOutput("x")
            seen.append(_VAR.get())  # advance 2: must see "set-by-advance-1"
            yield _FakeOutput("y")

        list(
            self._context_wrapper(gen).stream(
                INCOMING,
                None,
                CLIENT_ID,
                False,
                datetime.now(timezone.utc),
                0,
                self._TIMEOUT,
                "default",
            )
        )

        assert seen[0] == "set-in-caller", (
            f"Advance 1 saw {seen[0]!r} — ctx.run() is not propagating the "
            "caller's context to the worker"
        )
        assert seen[1] == "set-by-advance-1", (
            f"Advance 2 saw {seen[1]!r} — mutations from advance 1 are not "
            "preserved; each submit may be using a fresh copy_context()"
        )
