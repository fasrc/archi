"""Timeout guard in ``_prepare_chat_context`` (issue #175).

A falsey ``client_timeout`` or ``client_sent_msg_ts`` means the caller did not declare a
client-side deadline, so the server must not apply one.  The guard at ``app.py:1710``
must be conditional on *both* fields being truthy before comparing elapsed time against
the timeout value.

The explicit-deadline test (a non-zero pair where the window is genuinely exceeded) must
**pass both before and after the fix** — its purpose is to prove the guard was tightened,
not removed.  If the test were deleted rather than corrected it would give a false green on
a completely absent check.
"""

from datetime import datetime, timezone

import src.interfaces.chat_app.app as app_module
from src.interfaces.chat_app.app import ChatWrapper

CLIENT_ID = "client-1"
INCOMING = [["User", "hello"]]


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
            now.timestamp(),  # sent right now
            600.0,  # 10-minute deadline
            {},
        )

        assert error_code is None
        assert context is not None
