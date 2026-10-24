"""Unit tests for the Slack trace tags on /v1 spans (openspec/changes/slack-phoenix-tags).

A /v1 request whose X-OpenWebUI-Chat-Id starts with ``slack:`` gets the OpenInference
attributes session.id, tag.tags and metadata, so Phoenix can filter Slack traffic
and group each Slack thread as a session.
"""

import json
from unittest import mock
from unittest.mock import patch

import pytest
from flask import Flask
from openinference.instrumentation import get_attributes_from_context

import src.interfaces.chat_app.openai_compat as compat
from src.interfaces import slack_trace_tags
from src.interfaces.chat_app.openai_compat import register_openai_compat
from src.interfaces.slack_trace_tags import (
    SLACK_TAG,
    slack_question_span,
    slack_trace_attributes,
    tag_current_span,
    with_trace_attributes,
)

KEY = "slack:T1:C1:1700000000.000100"


# ---------------------------------------------------------------------------
# slack_trace_attributes
# ---------------------------------------------------------------------------


def test_slack_thread_key_gives_session_tag_and_metadata():
    assert slack_trace_attributes(KEY) == {
        "session_id": KEY,
        "tags": [SLACK_TAG],
        "metadata": {
            "source": "slack",
            "slack_team": "T1",
            "slack_channel": "C1",
            "slack_thread_ts": "1700000000.000100",
        },
    }


@pytest.mark.parametrize("chat_id", [None, "", "owui-123", "SLACK:T1:C1:1.0"])
def test_other_chat_ids_get_no_attributes(chat_id):
    assert slack_trace_attributes(chat_id) is None


def test_short_slack_key_keeps_the_tag_and_skips_missing_parts():
    assert slack_trace_attributes("slack:T1") == {
        "session_id": "slack:T1",
        "tags": [SLACK_TAG],
        "metadata": {"source": "slack", "slack_team": "T1"},
    }


# ---------------------------------------------------------------------------
# with_trace_attributes
# ---------------------------------------------------------------------------


def _context():
    return dict(get_attributes_from_context())


def test_attributes_are_active_during_each_step_only():
    seen = []

    def events():
        for n in (1, 2):
            seen.append(_context())
            yield n

    wrapped = with_trace_attributes(events(), slack_trace_attributes(KEY))
    first = next(wrapped)
    # Between steps the caller runs without the attributes.
    between = _context()
    rest = list(wrapped)
    assert [first, *rest] == [1, 2]
    assert between == {}
    assert _context() == {}
    for ctx in seen:
        assert ctx["session.id"] == KEY
        assert list(ctx["tag.tags"]) == [SLACK_TAG]
        assert json.loads(ctx["metadata"])["slack_channel"] == "C1"


def test_no_attributes_returns_the_events_unchanged():
    events = iter([1, 2])
    assert with_trace_attributes(events, None) is events


def test_a_consumer_that_stops_early_leaves_no_attributes():
    wrapped = with_trace_attributes(iter([1, 2, 3]), slack_trace_attributes(KEY))
    assert next(wrapped) == 1
    wrapped.close()
    assert _context() == {}


def test_an_error_in_the_events_leaves_no_attributes():
    def events():
        yield 1
        raise RuntimeError("model down")

    wrapped = with_trace_attributes(events(), slack_trace_attributes(KEY))
    assert next(wrapped) == 1
    with pytest.raises(RuntimeError, match="model down"):
        next(wrapped)
    assert _context() == {}


# ---------------------------------------------------------------------------
# tag_current_span
# ---------------------------------------------------------------------------


def test_tag_current_span_sets_the_openinference_attributes(monkeypatch):
    span = mock.Mock()
    monkeypatch.setattr(slack_trace_tags.trace, "get_current_span", lambda: span)
    tag_current_span(slack_trace_attributes(KEY))
    calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert calls["session.id"] == KEY
    assert calls["tag.tags"] == [SLACK_TAG]
    assert json.loads(calls["metadata"])["slack_team"] == "T1"


def test_tag_current_span_does_nothing_without_attributes(monkeypatch):
    span = mock.Mock()
    monkeypatch.setattr(slack_trace_tags.trace, "get_current_span", lambda: span)
    tag_current_span(None)
    span.set_attribute.assert_not_called()


def test_tag_current_span_never_raises(monkeypatch):
    span = mock.Mock()
    span.set_attribute.side_effect = RuntimeError("exporter gone")
    monkeypatch.setattr(slack_trace_tags.trace, "get_current_span", lambda: span)
    tag_current_span(slack_trace_attributes(KEY))


# ---------------------------------------------------------------------------
# /v1/chat/completions
# ---------------------------------------------------------------------------


@pytest.fixture
def app_and_seen():
    app = Flask(__name__)
    app.config["TESTING"] = True
    seen = []

    def stream(**_kwargs):
        seen.append(_context())
        yield {"type": "chunk", "content": "ok"}
        seen.append(_context())
        yield {
            "type": "final",
            "answer": "ok",
            "response": "ok",
            "source_documents": [],
            "retriever_scores": [],
        }

    wrapper = mock.MagicMock()
    wrapper.stream.side_effect = stream
    register_openai_compat(app, wrapper, auth_enabled=False)
    with (
        patch(
            "src.utils.config_access.get_full_config",
            return_value={"name": "test-model"},
        ),
        patch.object(compat, "_get_or_create_conversation", return_value=7),
        patch.object(compat, "tag_current_span") as tag_span,
    ):
        yield app, seen, tag_span
    compat._chat_wrapper = None
    compat._user_service = None


def _post(app, chat_id, stream):
    headers = {"X-OpenWebUI-Chat-Id": chat_id} if chat_id else {}
    return app.test_client().post(
        "/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": stream,
        },
        headers=headers,
    )


@pytest.mark.parametrize("stream", [False, True])
def test_slack_request_runs_the_answer_with_the_attributes(app_and_seen, stream):
    app, seen, tag_span = app_and_seen
    resp = _post(app, KEY, stream)
    resp.get_data()
    assert resp.status_code == 200
    assert seen and all(ctx["session.id"] == KEY for ctx in seen)
    tag_span.assert_called_once_with(slack_trace_attributes(KEY))


@pytest.mark.parametrize("chat_id", [None, "owui-123"])
def test_other_requests_run_without_attributes(app_and_seen, chat_id):
    app, seen, tag_span = app_and_seen
    resp = _post(app, chat_id, False)
    assert resp.status_code == 200
    assert seen and all(ctx == {} for ctx in seen)
    tag_span.assert_called_once_with(None)


# ---------------------------------------------------------------------------
# slack_question_span: the root span of a Slack turn, in the bot process
# ---------------------------------------------------------------------------


def _tracer():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def test_question_span_carries_session_tag_and_metadata(monkeypatch):
    monkeypatch.setattr(slack_trace_tags, "capture_content_enabled", lambda: False)
    tracer, exporter = _tracer()
    with slack_question_span(KEY, "quota?", tracer=tracer) as span:
        span.record_answer("a long answer")
    (finished,) = exporter.get_finished_spans()
    attrs = dict(finished.attributes)
    assert finished.name == "Slack question"
    assert finished.parent is None
    assert attrs["openinference.span.kind"] == "CHAIN"
    assert attrs["session.id"] == KEY
    assert attrs["tag.tags"] == (SLACK_TAG,)
    assert json.loads(attrs["metadata"])["slack_channel"] == "C1"
    # Content capture is off: no question or answer text on the span.
    assert "input.value" not in attrs
    assert "output.value" not in attrs


def test_question_span_records_text_only_with_content_capture(monkeypatch):
    monkeypatch.setattr(slack_trace_tags, "capture_content_enabled", lambda: True)
    tracer, exporter = _tracer()
    with slack_question_span(KEY, "quota?", tracer=tracer) as span:
        span.record_answer("an answer")
    attrs = dict(exporter.get_finished_spans()[0].attributes)
    assert attrs["input.value"] == "quota?"
    assert attrs["output.value"] == "an answer"


def test_question_span_is_the_parent_of_the_calls_inside_it():
    tracer, exporter = _tracer()
    with slack_question_span(KEY, "quota?", tracer=tracer):
        with tracer.start_as_current_span("POST"):
            pass
    child, root = exporter.get_finished_spans()
    assert child.parent.span_id == root.context.span_id


def test_question_span_records_an_error_and_reraises():
    tracer, exporter = _tracer()
    with pytest.raises(RuntimeError, match="chat app down"):
        with slack_question_span(KEY, "quota?", tracer=tracer):
            raise RuntimeError("chat app down")
    (finished,) = exporter.get_finished_spans()
    assert finished.status.status_code.name == "ERROR"


def test_question_span_uses_the_global_tracer_by_default(monkeypatch):
    tracer = mock.MagicMock()
    monkeypatch.setattr(slack_trace_tags.trace, "get_tracer", lambda name: tracer)
    with slack_question_span(KEY, "quota?"):
        pass
    assert tracer.start_as_current_span.call_args.args[0] == "Slack question"


def test_bot_answer_runs_the_chat_call_inside_the_question_span(monkeypatch):
    from src.interfaces import slack_bot

    spans = []

    class _Span:
        def record_answer(self, text):
            spans.append(("answer", text))

    class _Ctx:
        def __init__(self, chat_id, question):
            spans.append(("open", chat_id, question))

        def __enter__(self):
            return _Span()

        def __exit__(self, *exc):
            spans.append(("close",))
            return False

    monkeypatch.setattr(slack_bot, "slack_question_span", _Ctx)
    web = mock.Mock()
    web.chat_postMessage.return_value = {"ts": "99.0"}
    session = mock.Mock()

    def post(*args, **kwargs):
        spans.append(("post",))
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        return resp

    session.post.side_effect = post
    bot = slack_bot.SlackBot(
        web_client=web,
        socket_client=mock.Mock(),
        session=session,
        chat_url="http://chatbot:7861",
        bot_user_id="UBOT",
        executor=mock.Mock(),
    )
    bot.model = "m"
    event = {"type": "app_mention", "user": "U1", "text": "<@UBOT> quota?"}
    event.update({"ts": "10.0", "channel": "C1"})
    bot.answer(event, "T1")
    assert spans == [
        ("open", "slack:T1:C1:10.0", "quota?"),
        ("post",),
        ("answer", "ok"),
        ("close",),
    ]
