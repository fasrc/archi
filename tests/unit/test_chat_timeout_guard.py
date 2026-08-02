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
