"""Tag /v1 traces that come from the Slack bot, so Phoenix can show Slack traffic alone.

The Slack bot sends ``X-OpenWebUI-Chat-Id: slack:<team>:<channel>:<thread_ts>``
(``thread_key()`` in ``src/interfaces/slack_bot.py``). For such a request this module
puts three OpenInference attributes on the spans of the answer:

- ``session.id``: the thread key, so Phoenix's Sessions tab shows each Slack thread
  as one conversation;
- ``tag.tags``: ``["slack"]``;
- ``metadata``: ``source``, ``slack_team``, ``slack_channel`` and ``slack_thread_ts``.

In Phoenix, ``metadata['source'] == 'slack'`` then filters the Slack traffic.
"""

import json
from contextlib import contextmanager

from openinference.instrumentation import using_attributes
from opentelemetry import trace

from src.utils.logging import get_logger
from src.utils.telemetry import capture_content_enabled

logger = get_logger(__name__)

SLACK_TAG = "slack"
_PREFIX = "slack:"
_PARTS = ("slack_team", "slack_channel", "slack_thread_ts")


def slack_trace_attributes(external_chat_id):
    """Return the trace attributes for a Slack thread key, or None for other IDs."""
    if not external_chat_id or not external_chat_id.startswith(_PREFIX):
        return None
    metadata = {"source": SLACK_TAG}
    parts = external_chat_id[len(_PREFIX) :].split(":", len(_PARTS) - 1)
    for name, value in zip(_PARTS, parts):
        if value:
            metadata[name] = value
    return {
        "session_id": external_chat_id,
        "tags": [SLACK_TAG],
        "metadata": metadata,
    }


def _span_attributes(attributes):
    return {
        "session.id": attributes["session_id"],
        "tag.tags": list(attributes["tags"]),
        "metadata": json.dumps(attributes["metadata"]),
    }


class _QuestionSpan:
    """The span of one Slack turn, with a way to record the answer text."""

    def __init__(self, span):
        self._span = span

    def record_answer(self, text):
        if capture_content_enabled():
            self._span.set_attribute("output.value", text)


@contextmanager
def slack_question_span(chat_id, question, tracer=None):
    """Open the root span of one Slack turn in the bot process.

    The bot's outbound /v1 call is the root of each Slack trace, and Phoenix's
    Sessions view groups traces by the root span's ``session.id``. So the bot opens
    this span around the call. The question and answer text are recorded only when
    content capture is on (``capture_content_enabled``).
    """
    attributes = {
        "openinference.span.kind": "CHAIN",
        **_span_attributes(slack_trace_attributes(chat_id)),
    }
    if capture_content_enabled():
        attributes["input.value"] = question
    tracer = tracer or trace.get_tracer("archi.slack")
    with tracer.start_as_current_span("Slack question", attributes=attributes) as span:
        yield _QuestionSpan(span)


def tag_current_span(attributes):
    """Put the attributes on the current span (the Flask request span); never raises.

    The request span starts before the handler runs, so the context attributes
    of ``with_trace_attributes`` cannot reach it.
    """
    if not attributes:
        return
    try:
        span = trace.get_current_span()
        for key, value in _span_attributes(attributes).items():
            span.set_attribute(key, value)
    except Exception:
        logger.exception("failed to tag the request span for Slack")


def with_trace_attributes(events, attributes):
    """Iterate ``events`` with the attributes active during each step only.

    The spans of an answer (LangGraph, the retriever, the model call) start while
    the response pulls the next event, so the attributes must be active then. They
    are attached for one step at a time and detached before each yield: the context
    never stays attached across a yield, so a consumer that stops early or a step
    that raises leaves no attributes behind.
    """
    if not attributes:
        return events
    return _attributed(iter(events), attributes)


def _attributed(iterator, attributes):
    while True:
        with using_attributes(**attributes):
            try:
                event = next(iterator)
            except StopIteration:
                return
        yield event
