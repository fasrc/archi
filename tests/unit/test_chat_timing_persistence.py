"""What a ``timing`` row records when the client supplied no send time (issue #175).

Making ``client_sent_msg_ts`` optional means requests that omit it now *complete* instead
of being refused with 408 — so for the first time they reach ``insert_timing``.  There is
no representation for "unknown" in that column: ``timing.client_sent_msg_ts`` is
``TIMESTAMPTZ NOT NULL`` (``src/cli/templates/init.sql:476``), so the row must carry some
value.  The two candidates that keep the row are the Unix epoch and a fabricated
server-side substitute; the third option is to drop the row entirely.

The choice is the epoch, recorded here so it is a **specified sentinel rather than an
accident** of ``_parse_chat_request``'s falsey-coalesce.  A fabricated substitute (e.g.
``server_received_msg_ts``) would read as a genuinely instant client→server hop and be
indistinguishable from a real measurement; dropping the row would discard the ten
server-side milestones that *are* real, which is what the dashboards actually plot.

``1970-01-01T00:00:00Z`` therefore means "the client declared no send time", and callers
computing client→server latency must exclude it.  Making the column nullable is the
correct end state and is tracked separately — it cannot ship here, because the code change
would raise ``NotNullViolation`` on any deployment that has not run the migration, and
issue #180 documents that migrations are not applied to existing deployments.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from src.interfaces.chat_app.app import ChatWrapper

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class _FakeOutput:
    def __init__(self, answer):
        self.answer = answer
        self.metadata = {"event_type": "text"}

    def get(self, key, default=None):
        return default


def _wrapper(captured):
    """A ChatWrapper stubbed to what ``stream`` touches, per test_chat_override_persistence."""
    wrapper = object.__new__(ChatWrapper)
    wrapper.archi = SimpleNamespace(
        stream=lambda **kwargs: iter([_FakeOutput("partial")]),
        pipeline_name="FakePipeline",
        supports_stream=lambda pipeline=None: True,
    )
    wrapper.current_model_used = "default/default"
    wrapper.number_of_queries = 0
    wrapper.cursor = None
    wrapper.conn = None

    wrapper._init_timestamps = lambda: {}
    wrapper._resolve_config_name = lambda name: name or "default"
    wrapper.update_config = lambda config_name=None: None
    wrapper.create_agent_trace = lambda **kwargs: None
    wrapper.update_agent_trace = lambda **kwargs: None
    wrapper._prepare_chat_context = lambda message, conversation_id, *a, **k: (
        SimpleNamespace(conversation_id=conversation_id, history=[]),
        None,
    )
    wrapper._finalize_result = lambda result, **kwargs: ("out", [10])

    def insert_timing(message_id, timestamps):
        captured["message_id"] = message_id
        captured["timestamps"] = dict(timestamps)

    wrapper.insert_timing = insert_timing
    return wrapper


def _stream(captured, client_sent_msg_ts):
    list(
        _wrapper(captured).stream(
            message=["hi"],
            conversation_id=101,
            client_id="c",
            is_refresh=False,
            server_received_msg_ts=datetime.now(timezone.utc),
            client_sent_msg_ts=client_sent_msg_ts,
            client_timeout=0,
            config_name="default",
        )
    )


class TestAnAbsentClientSendTimeIsRecordedAsTheEpochSentinel:
    def test_a_timestampless_request_still_writes_its_timing_row(self):
        """The nine server-side milestones are real measurements and must not be lost."""
        captured = {}
        _stream(captured, 0)

        assert captured["message_id"] == 10
        assert captured["timestamps"]["server_response_msg_ts"] is not None

    def test_the_absent_send_time_is_the_epoch_and_not_a_server_side_substitute(self):
        # The sentinel must stay distinguishable from a real measurement. Substituting
        # server_received_msg_ts here would record a zero-latency client hop that no
        # query could tell apart from a genuinely fast request.
        captured = {}
        _stream(captured, 0)

        recorded = captured["timestamps"]["client_sent_msg_ts"]
        assert recorded == EPOCH
        assert recorded != captured["timestamps"]["server_received_msg_ts"]

    def test_a_supplied_send_time_is_persisted_unchanged(self):
        """The discriminating case: a real value must not be flattened to the sentinel."""
        captured = {}
        sent_at = 1_700_000_000.5
        _stream(captured, sent_at)

        assert captured["timestamps"]["client_sent_msg_ts"] == datetime.fromtimestamp(
            sent_at, tz=timezone.utc
        )
        assert captured["timestamps"]["client_sent_msg_ts"] != EPOCH
