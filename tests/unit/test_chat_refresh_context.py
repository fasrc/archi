"""`is_refresh` must have prior turns to refresh (issue #177).

``_prepare_chat_context`` resolves history from one of three sources, then applies two
refresh-dependent steps: it trims trailing assistant turns (``app.py:1650-1652``) and it
skips appending the caller's message when the request is a refresh (``:1657-1658``).

Combine ``is_refresh`` with *no* history source and both steps degenerate: the trim is a
no-op on an empty list and the append never happens, so the pipeline is invoked with **no
user turn at all** and answers an empty prompt. Nothing is raised and nothing is logged, so
the caller cannot distinguish it from a real answer — which is why the fix is an explicit
rejection rather than a best-effort interpretation.

The condition is the absence of *prior turns*, not the absence of a ``conversation_id``.
``external_history`` is also a source of prior turns, and a refresh over supplied history is
coherent. ``test_refresh_over_supplied_history_is_honoured`` is the test that holds the guard
to that distinction: it fails if the guard is written as "reject when ``conversation_id`` is
None", which is the obvious and wrong way to write it.

These tests drive the real method with a stub ``self``. Note that every other test file
referencing ``_prepare_chat_context`` *replaces* it to exercise callers, so the real body is
otherwise executed by no test.
"""

from datetime import datetime, timezone

import pytest

import src.interfaces.chat_app.app as app_module
from src.interfaces.chat_app.app import ARCHI_SENDER, ChatWrapper

CLIENT_ID = "client-1"
INCOMING = [["User", "hello"]]


def _wrapper(created, stored_history=None):
    """A ChatWrapper carrying only the collaborators this method touches."""
    wrapper = object.__new__(ChatWrapper)

    def create_conversation(first_message, client_id, user_id=None):
        created.append(first_message)
        return 42

    wrapper.create_conversation = create_conversation
    wrapper.query_conversation_history = (
        lambda conversation_id, client_id, user_id=None: list(stored_history or [])
    )
    wrapper.update_conversation_timestamp = (
        lambda conversation_id, client_id, user_id=None: None
    )
    return wrapper


def _prepare(wrapper, *, is_refresh, conversation_id=None, external_history=None):
    """Call the real method with a timing pair that does not trip the #175 check.

    ``app.py:1654`` compares ``server_received - client_sent`` against ``client_timeout``
    with no guard. Sending the same instant for both, with a generous timeout, keeps that
    unrelated bug out of these results.
    """
    now = datetime.now(timezone.utc)
    return wrapper._prepare_chat_context(
        INCOMING,
        conversation_id,
        CLIENT_ID,
        is_refresh,
        now,
        now.timestamp(),
        600.0,
        {},
        user_id=None,
        external_history=external_history,
    )


class TestRefreshWithoutPriorTurns:
    def test_refresh_with_no_history_source_is_rejected(self):
        context, error_code = _prepare(_wrapper([]), is_refresh=True)

        assert context is None
        assert error_code == 400

    def test_a_rejected_refresh_creates_no_conversation(self):
        created = []

        _prepare(_wrapper(created), is_refresh=True)

        # The guard has to run before the branch at app.py:1639-1641, or a request the
        # server refuses still leaves an empty conversation row behind.
        assert created == []


class TestRefreshWithPriorTurnsIsUnaffected:
    def test_refresh_over_supplied_history_is_honoured(self):
        supplied = [("User", "q1"), (ARCHI_SENDER, "a1")]

        context, error_code = _prepare(
            _wrapper([]), is_refresh=True, external_history=list(supplied)
        )

        # `external_history` is a source of prior turns, so this refresh is satisfiable
        # even with no conversation_id. A guard keyed on conversation_id alone would
        # reject it — this test is what forbids that.
        assert error_code is None
        assert context.history == [("User", "q1")]

    def test_refresh_against_an_existing_conversation_is_unchanged(self):
        stored = [("User", "q1"), (ARCHI_SENDER, "a1")]

        context, error_code = _prepare(
            _wrapper([], stored_history=stored), is_refresh=True, conversation_id=7
        )

        assert error_code is None
        assert context.history == [("User", "q1")]
        # The incoming message is deliberately NOT appended on a refresh, so the fix
        # cannot be "always append".
        assert ("User", "hello") not in context.history

    def test_a_first_message_that_is_not_a_refresh_is_unchanged(self):
        created = []

        context, error_code = _prepare(_wrapper(created), is_refresh=False)

        assert error_code is None
        assert context.history == [("User", "hello")]
        assert created == ["hello"]


class TestChatErrorMessage:
    """One shared mapping, so a status cannot be described on one endpoint only."""

    def test_400_names_the_unsatisfiable_refresh(self):
        message = app_module._chat_error_message(400)

        assert "refresh" in message.lower()
        assert message != "server error; see chat logs for message"

    def test_408_keeps_its_existing_text(self):
        assert app_module._chat_error_message(408) == (
            app_module.CLIENT_TIMEOUT_ERROR_MESSAGE
        )

    def test_403_keeps_its_existing_text(self):
        assert app_module._chat_error_message(403) == "conversation not found"

    def test_an_unrecognized_status_falls_back_to_the_generic_text(self):
        assert (
            app_module._chat_error_message(500)
            == "server error; see chat logs for message"
        )


class TestStreamingSurfacesTheMessage:
    def test_the_streaming_endpoint_reports_400_with_the_shared_message(self):
        wrapper = object.__new__(ChatWrapper)
        wrapper._init_timestamps = lambda: {}
        wrapper._prepare_chat_context = lambda *a, **k: (None, 400)
        # Only the `finally` at app.py:2588 is reached beyond the error branch.
        wrapper.cursor = None
        wrapper.conn = None

        events = list(
            wrapper.stream(
                message=INCOMING,
                conversation_id=None,
                client_id=CLIENT_ID,
                is_refresh=True,
                server_received_msg_ts=datetime.now(timezone.utc),
                client_sent_msg_ts=datetime.now(timezone.utc).timestamp(),
                client_timeout=600.0,
                config_name="default",
            )
        )

        # The stream is already open, so the rejection can only arrive in-band.
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["status"] == 400
        assert events[0]["message"] == app_module._chat_error_message(400)
