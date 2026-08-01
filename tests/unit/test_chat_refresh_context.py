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

import json
from datetime import datetime, timezone

import pytest
from flask import Flask

import src.interfaces.chat_app.app as app_module
from src.interfaces.chat_app.app import ARCHI_SENDER, ChatWrapper

CLIENT_ID = "client-1"
INCOMING = [["User", "hello"]]


def _wrapper(created, stored_history=None, touched=None):
    """A ChatWrapper carrying only the collaborators this method touches.

    ``created`` records conversation creations and ``touched`` timestamp updates, so a
    test can assert that a refused request writes nothing.
    """
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

        # History resolution has to stay side-effect free until the request is known to
        # be serviceable, or a refusal still leaves an empty conversation row behind.
        assert created == []

    @pytest.mark.parametrize(
        "label, kwargs",
        [
            ("supplied history is empty", {"external_history": []}),
            (
                "supplied history is assistant turns only",
                {"external_history": [(ARCHI_SENDER, "a1")]},
            ),
            ("named conversation holds no turns", {"conversation_id": 7}),
        ],
    )
    def test_a_refresh_with_no_surviving_turn_is_rejected(self, label, kwargs):
        """Three routes to the same unsatisfiable state.

        Guarding on *which fields were supplied* is a proxy for "prior turns exist",
        and the proxy fails all three of these: an empty supplied list is not ``None``,
        an assistant-only history is emptied by the trim, and a named conversation can
        hold no turns. Only the resolved, post-trim history distinguishes them.
        """
        created = []

        context, error_code = _prepare(
            _wrapper(created, stored_history=[]), is_refresh=True, **kwargs
        )

        assert context is None, label
        assert error_code == 400, label
        assert created == [], label


class TestPreconditions:
    def test_a_missing_client_id_raises(self):
        """`client_id` is required before any history work happens.

        Covered here because this change moved code around that guard and diff-cover
        attributes the line to the diff; it is also a real precondition that had no
        test, so pinning it is worth more than arguing with the diff algorithm.
        """
        with pytest.raises(ValueError, match="client_id is required"):
            _wrapper([])._prepare_chat_context(
                INCOMING,
                None,
                "",
                False,
                datetime.now(timezone.utc),
                datetime.now(timezone.utc).timestamp(),
                600.0,
                {},
            )


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


class TestSideEffectsStillHappenWhenTheRequestIsServed:
    """The writes moved after the refresh check; prove they still fire correctly."""

    def test_an_existing_conversation_has_its_timestamp_updated(self):
        touched = []

        _prepare(
            _wrapper([], stored_history=[("User", "q1")], touched=touched),
            is_refresh=False,
            conversation_id=7,
        )

        assert touched == [7]

    def test_supplied_history_does_not_touch_the_conversation_timestamp(self):
        touched = []

        _prepare(
            _wrapper([], touched=touched),
            is_refresh=False,
            conversation_id=7,
            external_history=[("User", "q1")],
        )

        # The external branch never updated the timestamp before this change either;
        # the restructure must not quietly start doing it.
        assert touched == []

    def test_a_refused_refresh_updates_no_timestamp(self):
        touched = []

        _prepare(
            _wrapper([], stored_history=[], touched=touched),
            is_refresh=True,
            conversation_id=7,
        )

        assert touched == []

    def test_supplied_history_is_not_mutated_by_the_refresh_trim(self):
        supplied = [("User", "q1"), (ARCHI_SENDER, "a1")]

        _prepare(_wrapper([]), is_refresh=True, external_history=supplied)

        # The trim pops from the resolved list; that list must be a copy, because
        # mutating the caller's argument is not this function's to do.
        assert supplied == [("User", "q1"), (ARCHI_SENDER, "a1")]


class TestTheRoutesThemselves:
    """Drive the real Flask view functions, not just the context helper.

    The unit tests above call `_prepare_chat_context` directly and the streaming test
    substitutes it, so neither exercises the HTTP layer where the status code is
    actually chosen. These do: they register the real view functions on a Flask app and
    assert what a caller receives — the non-streaming `400`, and the streaming
    endpoint's `200` carrying an in-band error. That difference is the whole point of
    the two-error-channels section in the API reference, and it is invisible below the
    route.
    """

    @staticmethod
    def _app(view_name, chat):
        wrapper = object.__new__(app_module.FlaskAppWrapper)
        wrapper.chat = chat
        flask_app = Flask(__name__)
        flask_app.secret_key = "test"
        flask_app.add_url_rule(
            "/api/x", view_name, getattr(wrapper, view_name), methods=["POST"]
        )
        return flask_app

    @staticmethod
    def _body(**over):
        body = {
            "last_message": INCOMING,
            "client_id": CLIENT_ID,
            "is_refresh": True,
            "client_sent_msg_ts": 1,
            "client_timeout": 600000,
        }
        body.update(over)
        return body

    def test_non_streaming_route_returns_a_real_http_400(self):
        flask_app = self._app(
            "get_chat_response", lambda *a, **k: (None, None, None, {}, 400)
        )

        with flask_app.test_client() as client:
            response = client.post("/api/x", json=self._body())

        assert response.status_code == 400
        assert response.get_json() == {
            "error": app_module.REFRESH_WITHOUT_HISTORY_ERROR_MESSAGE
        }

    def test_non_streaming_route_still_maps_408_and_403(self):
        for code, expected in (
            (408, app_module.CLIENT_TIMEOUT_ERROR_MESSAGE),
            (403, "conversation not found"),
        ):
            flask_app = self._app(
                "get_chat_response", lambda *a, **k: (None, None, None, {}, code)
            )
            with flask_app.test_client() as client:
                response = client.post("/api/x", json=self._body())

            assert response.status_code == code
            assert response.get_json() == {"error": expected}

    def test_streaming_route_returns_http_200_with_the_error_in_band(self):
        class _Chat:
            def stream(self, *a, **k):
                yield {"type": "meta", "event": "stream_started"}
                yield {
                    "type": "error",
                    "status": 400,
                    "message": app_module.REFRESH_WITHOUT_HISTORY_ERROR_MESSAGE,
                }

        flask_app = self._app("get_chat_response_stream", _Chat())

        with flask_app.test_client() as client:
            response = client.post("/api/x", json=self._body())
            events = [
                json.loads(line)
                for line in response.get_data(as_text=True).splitlines()
                if line.strip()
            ]

        # The rejection is a 400, but the HTTP status is 200 — a client checking only
        # the status reads this as success.
        assert response.status_code == 200
        assert events[-1]["status"] == 400
        assert events[-1]["message"] == (
            app_module.REFRESH_WITHOUT_HISTORY_ERROR_MESSAGE
        )


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
