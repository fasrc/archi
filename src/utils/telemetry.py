"""OpenTelemetry bootstrap for archi services.

This module is off unless an operator turns it on, it fails open, and it never
raises into its caller. ``setup_logging()`` calls ``init_telemetry()``, and
``setup_logging()`` is the first statement of every service entrypoint in
``src/bin/``, so an exception here would stop a service before it opens a port.

Two environment variables turn tracing on:

``OTEL_EXPORTER_OTLP_ENDPOINT``
    The OTLP over HTTP address of a receiver. Setting it is enough.
``ARCHI_OTEL_ENABLED``
    A true value turns tracing on with no endpoint, which records spans without
    exporting them. A false value turns tracing off even when an endpoint is set,
    so one service can opt out of an endpoint in a shared ``.env`` file.

Content stays out of spans. See ``ARCHI_OTEL_CAPTURE_CONTENT`` in the operator
documentation, and ``docs/docs/proposals/opentelemetry-readiness.md`` section 2.7
for why the default is a code default and not advice about an environment.

This module imports no OpenTelemetry package at module scope. Every import happens
inside a function, behind the enable check, so a deployment without the packages
loads this file and keeps running.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import sys
import threading
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

# Deliberately the standard library logger, not src.utils.logging.get_logger:
# src.utils.logging imports this module, and get_logger would close the cycle.
logger = logging.getLogger(__name__)

ENABLE_VAR = "ARCHI_OTEL_ENABLED"
ENDPOINT_VAR = "OTEL_EXPORTER_OTLP_ENDPOINT"
SERVICE_NAME_VAR = "OTEL_SERVICE_NAME"
CAPTURE_CONTENT_VAR = "ARCHI_OTEL_CAPTURE_CONTENT"

DEFAULT_SERVICE_NAME = "archi"
SERVICE_NAME_PREFIX = "archi-"
_ENTRYPOINT_PREFIX = "service_"

PLAIN_LOG_FORMAT = "(%(asctime)s) [%(name)s] %(levelname)s: %(message)s"
TRACE_LOG_FORMAT = (
    "(%(asctime)s) [%(name)s] %(levelname)s "
    "[trace_id=%(otelTraceID)s span_id=%(otelSpanID)s]: %(message)s"
)

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})

# Instrumentors that patch a library globally, as (name, module, class). The Flask
# instrumentor is absent on purpose: every entrypoint binds ``Flask`` with
# ``from flask import Flask`` before this module runs, so a global patch of
# ``flask.Flask`` would never reach the app object the entrypoint builds.
# ``instrument_flask_app()`` handles that seam per application instead.
_INSTRUMENTORS: Tuple[Tuple[str, str, str], ...] = (
    ("requests", "opentelemetry.instrumentation.requests", "RequestsInstrumentor"),
    ("httpx", "opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"),
    ("urllib", "opentelemetry.instrumentation.urllib", "URLLibInstrumentor"),
    ("psycopg2", "opentelemetry.instrumentation.psycopg2", "Psycopg2Instrumentor"),
    ("sqlite3", "opentelemetry.instrumentation.sqlite3", "SQLite3Instrumentor"),
    (
        "langchain",
        "openinference.instrumentation.langchain",
        "LangChainInstrumentor",
    ),
    ("logging", "opentelemetry.instrumentation.logging", "LoggingInstrumentor"),
)

# Arguments a particular instrumentor needs, built when it is installed.
#
# logging: the record factory adds otelTraceID and otelSpanID only when it is asked
# to inject context. The instrumentor asks for that itself when it is also told to
# own the log format, and it then calls basicConfig with a format of its own.
# setup_logging() owns the format here, so the injection has to be requested
# directly and the format left alone.
#
# langchain: this is the seam that gives Phoenix its span kinds, and the one that
# would otherwise export every prompt. See build_trace_config().
_INSTRUMENTOR_KWARGS = {
    "logging": lambda: {"inject_trace_context": True, "set_logging_format": False},
    "langchain": lambda: {"config": build_trace_config()},
}

_LOGGING_INSTRUMENTOR = "logging"

# URL-valued attributes whose query string comes off before export, and attributes
# that hold nothing but a query string and so come off whole. The SSO redirect route
# receives an OAuth authorization code here, and the catalog search receives the
# user's own words. Neither is model content, so ARCHI_OTEL_CAPTURE_CONTENT does not
# reach them.
_URL_ATTRIBUTES = frozenset({"http.url", "url.full", "http.target", "url.path"})
_QUERY_ATTRIBUTES = frozenset({"url.query"})

# Routes that get no span at all. The OAuth callback
# (src/interfaces/chat_app/app.py:3321,3457-3464) receives an authorization code in
# its query string. A request that carries a credential is better off with no span
# than with a span whose safety depends on the scrubber above. The value is a
# comma-separated list of regular expressions, and the instrumentor searches each
# one against the request URL.
EXCLUDED_URLS = r"/redirect(\?|$)"

# Attribute keys whose value is retrieved text rather than a measurement. The leaf
# name is what identifies them, so this matches retrieval documents and reranker
# documents alike.
#
# OpenInference does not cover these. Measured on 2026-09-09 with
# openinference-instrumentation 0.1.62: TraceConfig(hide_inputs=True,
# hide_outputs=True).mask() returns "retrieval.documents.0.document.content"
# unchanged. Its mask table (openinference/instrumentation/config.py:335-430) has a
# case for reranker documents and none for retrieval documents, so a retriever span
# would otherwise carry the text of every chunk the knowledge base returned.
#
# Enforcing it here rather than in the config also means the guarantee does not
# depend on a table inside a dependency.
_DOCUMENT_CONTENT_SUFFIXES = ("document.content", "document.metadata")
REDACTED_VALUE = "__REDACTED__"

# Path markers after which a URL is a credential rather than a route. archi posts to
# two webhooks whose whole URL comes out of a secret: Mattermost
# (src/interfaces/mattermost.py:59) and Slack (src/interfaces/piazza.py:88). The
# requests instrumentor records that URL in http.url, and stripping the query string
# does nothing for either, because the token sits in the path.
#
# Everything up to and including the marker survives, so a reader still sees which
# host was called. Ordinary paths are untouched: a rule that ate every path would
# make the traces useless.
_CREDENTIAL_PATH_MARKERS = ("/hooks/", "/services/")

# The statement a database span records. Nearly every archi statement is
# parameterised, so the values reach the driver separately and the statement text is
# only shape. SQL_INSERT_CONVO (src/utils/sql.py:7) is the exception: it goes through
# psycopg2.extras.execute_values, which expands the rows into the statement before
# sending it. That is correct use of the driver, and it leaves the finished statement
# holding the question and the whole answer — which is what the instrumentor records.
#
# Measured on the deployed check, not derived: one streamed chat request put a user's
# question and archi's reply into db.statement on the span named after the database.
#
# The literals go and the shape stays, so a database span still names its table and
# its kind of statement. Numbers are left alone: a bare number is an identifier or a
# limit, never the conversation, and losing them would cost real debugging value.
_STATEMENT_ATTRIBUTES = frozenset({"db.statement", "db.query.text"})

# Three ways Postgres writes a string, in the order they must be tried. A dollar-quoted
# body can hold anything including quotes; an E'' string escapes with a backslash; an
# ordinary string escapes a quote by doubling it. A rule that knew only the last form
# would stop early on the other two and leave the rest of the row in the clear.
#
# The dollar tag follows unquoted-identifier rules, which are not ASCII: any letter
# will do. [^\W\d] is a word character that is not a digit, which is a letter or an
# underscore in any script, and rejecting a leading digit is what keeps a bound
# parameter like $1 from being read as the start of a quoted body.
_SQL_LITERAL = re.compile(
    r"\$(?P<tag>[^\W\d]\w*|)\$.*?\$(?P=tag)\$"
    r"|[Ee]'(?:[^'\\]|\\.|'')*'"
    r"|'(?:[^']|'')*'",
    re.DOTALL,
)

# An array of nothing but numbers, which in archi means one chunk embedding. The
# ingest writes the vector inline: measured on the deployed check, one chunk insert
# carried an 8388 character statement that was nearly all float array. The literal
# rule cannot reach it, because a number is not a quoted string.
#
# An embedding is the chunk in another form, and it costs every ingest trace real
# bytes. Arrays that are not all numbers are left alone, and so is a number standing
# on its own — that one is an identifier or a limit.
_SQL_NUMBER_ARRAY = re.compile(r"ARRAY\s*\[[\s,]*[-+0-9][-+0-9.eE,\s]*\]")

# Free-text fields that can quote a URL. An exception message and a stack trace are
# not attributes, so scrubbing only span.attributes lets the same secret out through
# a different door.
#
# The second branch matches a relative path, and it has to. The commonest requests
# failure does not quote an absolute URL at all: urllib3 raises "Max retries exceeded
# with url: /redirect?code=…". Matching every path is harmless, because _scrub_url()
# returns an ordinary path unchanged.
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>)\]]+|/[^\s\"'<>)\]]*")

# Event attributes that are content until proven otherwise. An exception message can
# carry model output and often does: src/archi/providers/huit_bedrock_provider.py:270
# puts the first 500 characters of the upstream response body into a RuntimeError,
# and the LangChain instrumentor records that string on the span. hide_inputs and
# hide_outputs cover input and output attributes, not arbitrary exception text.
#
# No rule can tell a message that quotes a model from one that does not, so on the
# default path these do not leave the host. The service log still holds both in full,
# which is where an operator debugging a failure is already looking.
_CONTENT_BEARING_EVENT_ATTRIBUTES = frozenset(
    {"exception.message", "exception.stacktrace"}
)

# The OTLP exporter logs its own error on every retry of every batch. Reporting once
# from the wrapper while this logger repeats underneath is the same flood with an
# extra line, so the wrapper holds this one back for the length of a failure streak.
_INNER_EXPORTER_LOGGER = "opentelemetry.exporter.otlp.proto.http.trace_exporter"


@dataclass(frozen=True)
class TelemetryStatus:
    """What ``init_telemetry()`` did, so a caller can act on it without guessing."""

    enabled: bool = False
    log_correlation: bool = False
    exporting: bool = False
    service_name: Optional[str] = None
    failures: Tuple[str, ...] = ()
    tracer_provider: Any = None

    @property
    def log_format(self) -> str:
        """The format string the root handler must use.

        The trace-aware format reads record fields that only the logging
        instrumentor installs. Asking for it in any other case makes every later
        record die in formatting, and the first record to die would be the warning
        that reports the failure.
        """
        return TRACE_LOG_FORMAT if self.log_correlation else PLAIN_LOG_FORMAT


_LOCK = threading.RLock()
_STATUS: Optional[TelemetryStatus] = None
_INSTALLED: list = []


def _is_true(value: str) -> bool:
    return value.strip().lower() in _TRUE_VALUES


def _is_false(value: str) -> bool:
    return value.strip().lower() in _FALSE_VALUES


def telemetry_requested() -> bool:
    """Report whether the operator asked for telemetry.

    The archi flag wins in both directions when it holds a value this module
    recognises. Otherwise the presence of an endpoint decides.
    """
    flag = os.getenv(ENABLE_VAR, "")
    if flag.strip():
        if _is_true(flag):
            return True
        if _is_false(flag):
            return False
    return bool(os.getenv(ENDPOINT_VAR, "").strip())


def capture_content_enabled() -> bool:
    """Report whether spans may carry model inputs and outputs.

    This flag never releases a URL query string. An authorization code is not
    model content, and no flag exports one.
    """
    return _is_true(os.getenv(CAPTURE_CONTENT_VAR, ""))


def resolve_service_name(service_name: Optional[str] = None) -> str:
    """Name this process: the environment, then the argument, then the entrypoint."""
    from_env = os.getenv(SERVICE_NAME_VAR, "").strip()
    if from_env:
        return from_env
    if service_name and service_name.strip():
        return service_name.strip()
    return _name_from_entrypoint(sys.argv[0] if sys.argv else "")


def _name_from_entrypoint(argv0: str) -> str:
    """Derive ``archi-chat`` from ``src/bin/service_chat.py``.

    Only an entrypoint under ``src/bin/`` carries a name worth deriving. Anything
    else -- a test runner, ``python -c``, a shell -- gets the generic name rather
    than a name that reads like a service and is not one.
    """
    stem = Path(argv0).stem if argv0 else ""
    if not stem.startswith(_ENTRYPOINT_PREFIX):
        return DEFAULT_SERVICE_NAME
    suffix = stem[len(_ENTRYPOINT_PREFIX) :].replace("_", "-")
    return SERVICE_NAME_PREFIX + suffix if suffix else DEFAULT_SERVICE_NAME


def init_telemetry(service_name: Optional[str] = None) -> TelemetryStatus:
    """Start tracing if an operator asked for it, and never raise.

    The call is idempotent. A second call returns the status of the first without
    installing anything again.
    """
    global _STATUS
    with _LOCK:
        if _STATUS is not None:
            return _STATUS
        try:
            _STATUS = _start(service_name)
        except Exception as exc:  # noqa: BLE001 - fail open, always
            logger.warning(
                "OpenTelemetry did not start, continuing without telemetry: %s", exc
            )
            _STATUS = TelemetryStatus(failures=("bootstrap",))
        return _STATUS


def _start(service_name: Optional[str]) -> TelemetryStatus:
    if not telemetry_requested():
        return TelemetryStatus()

    name = resolve_service_name(service_name)
    provider = _build_tracer_provider(name)
    exporting = _attach_exporter(provider)
    _set_global_tracer_provider(provider)
    failures = _install_instrumentors(provider)

    return TelemetryStatus(
        enabled=True,
        log_correlation=_LOGGING_INSTRUMENTOR not in failures,
        exporting=exporting,
        service_name=name,
        failures=tuple(failures),
        tracer_provider=provider,
    )


def _attach_exporter(provider) -> bool:
    """Ship spans when an endpoint exists, and record them without shipping otherwise.

    A process with the archi flag set and no endpoint is a valid state: the
    instrumentors run and a test or a debugger can read the spans. Nothing leaves
    the host.
    """
    endpoint = os.getenv(ENDPOINT_VAR, "").strip()
    if not endpoint:
        return False

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider.add_span_processor(
        BatchSpanProcessor(RedactingSpanExporter(OTLPSpanExporter()))
    )
    return True


def build_trace_config():
    """Build the OpenInference config that decides whether content leaves the host.

    The default is a code default, not advice about an environment. OpenInference
    reads several environment variables, and one that an operator misses exports
    every prompt. Each field is named here so no default of theirs decides this.
    """
    from openinference.instrumentation import TraceConfig

    hide = not capture_content_enabled()
    return TraceConfig(
        hide_inputs=hide,
        hide_outputs=hide,
        hide_input_messages=hide,
        hide_output_messages=hide,
        hide_input_text=hide,
        hide_output_text=hide,
        hide_prompts=hide,
        hide_choices=hide,
        hide_embeddings_text=hide,
    )


def _build_tracer_provider(service_name: str):
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    return TracerProvider(resource=Resource.create({"service.name": service_name}))


def _set_global_tracer_provider(provider) -> None:
    from opentelemetry import trace

    trace.set_tracer_provider(provider)


def _scrub_events(events, redact_content: bool = True):
    """Return cleaned events, or None when nothing needed cleaning.

    Two rules. An exception message and a stack trace are content, so they do not
    leave the host unless the operator turned content capture on. Every other string
    keeps its text and loses only the credentials inside any URL it quotes.
    """
    if not events:
        return None

    from opentelemetry.sdk.trace import Event

    rebuilt = []
    changed = False
    for event in events:
        attributes = dict(event.attributes or {})
        for key, value in attributes.items():
            if not isinstance(value, str):
                continue
            if redact_content and key in _CONTENT_BEARING_EVENT_ATTRIBUTES:
                attributes[key] = REDACTED_VALUE
                changed = True
                continue
            scrubbed = _scrub_text(value)
            if scrubbed != value:
                attributes[key] = scrubbed
                changed = True
        rebuilt.append(
            Event(name=event.name, attributes=attributes, timestamp=event.timestamp)
        )
    return rebuilt if changed else None


def _scrub_status(status, redact_content: bool = True):
    """Return a cleaned status, or None when the description needed no cleaning.

    The SDK builds this description from the exception that ended the span, so it
    carries the same risk as the exception message and answers to the same flag. The
    status code survives either way, so a reader still sees that the span failed.
    """
    description = getattr(status, "description", None)
    if not description:
        return None

    scrubbed = REDACTED_VALUE if redact_content else _scrub_text(description)
    if scrubbed == description:
        return None

    from opentelemetry.trace import Status

    return Status(status_code=status.status_code, description=scrubbed)


def instrument_flask_app(app) -> bool:
    """Give one Flask application a server span per request, and never raise.

    Call it once per application, right after the application is built. It calls
    ``init_telemetry()`` itself, which is idempotent, so an entrypoint needs one
    line and no knowledge of the order.

    The SSO redirect route is excluded rather than scrubbed. It receives an OAuth
    authorization code, and a request that carries a credential is better off with
    no span at all than with a span someone has to trust the scrubber for.
    """
    status = init_telemetry()
    if not status.enabled:
        return False
    try:
        from opentelemetry.instrumentation.flask import FlaskInstrumentor

        FlaskInstrumentor().instrument_app(
            app,
            tracer_provider=status.tracer_provider,
            excluded_urls=EXCLUDED_URLS,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - fail open, always
        logger.warning(
            "OpenTelemetry did not instrument the Flask application, "
            "continuing without request spans: %s",
            exc,
        )
        return False


def _is_document_content(key: str) -> bool:
    return key.endswith(_DOCUMENT_CONTENT_SUFFIXES)


def _scrub_url(value: str) -> str:
    """Drop the query string, and drop a credential that sits in the path."""
    cleaned = value.split("?", 1)[0]
    for marker in _CREDENTIAL_PATH_MARKERS:
        index = cleaned.find(marker)
        if index != -1:
            return cleaned[: index + len(marker)] + REDACTED_VALUE
    return cleaned


def _scrub_text(value: str) -> str:
    """Apply the URL rules to every URL inside a free-text field."""
    return _URL_IN_TEXT.sub(lambda match: _scrub_url(match.group(0)), value)


def _scrub_statement(value: str) -> str:
    """Replace every string literal and every numeric array, and keep the shape."""
    return _SQL_NUMBER_ARRAY.sub("ARRAY[?]", _SQL_LITERAL.sub("'?'", value))


def _scrub_attributes(attributes, redact_content: bool = True):
    """Return cleaned attributes, or None when nothing needed cleaning.

    Two rules, and they answer to different switches. A query string comes off
    always, because it can carry an authorization code. Document text comes off
    unless the operator turned content capture on.
    """
    if not attributes:
        return None

    cleaned = {}
    changed = False
    for key, value in attributes.items():
        if key in _QUERY_ATTRIBUTES:
            changed = True
            continue
        if key in _URL_ATTRIBUTES and isinstance(value, str):
            scrubbed = _scrub_url(value)
            if scrubbed != value:
                cleaned[key] = scrubbed
                changed = True
                continue
        if redact_content and _is_document_content(key):
            cleaned[key] = REDACTED_VALUE
            changed = True
            continue
        if redact_content and key in _STATEMENT_ATTRIBUTES and isinstance(value, str):
            scrubbed = _scrub_statement(value)
            if scrubbed != value:
                cleaned[key] = scrubbed
                changed = True
                continue
        cleaned[key] = value
    return cleaned if changed else None


class RedactingSpanExporter:
    """Wrap an exporter: take the query strings off, and say when export fails.

    This is a plain class rather than a subclass of ``SpanExporter`` on purpose.
    Subclassing needs an OpenTelemetry import at module scope, and this module
    imports nothing until an operator turns telemetry on. The span processors call
    ``export``, ``shutdown`` and ``force_flush`` by name and check no type.

    A batch processor drops spans in silence when export fails, so this wrapper is
    the only place that can report it. It reports once per run of failures. A broken
    receiver must not turn one problem into a flood of log lines.
    """

    def __init__(self, inner, redact_content: Optional[bool] = None):
        self._inner = inner
        self._failing = False
        self._redact_content = (
            not capture_content_enabled() if redact_content is None else redact_content
        )
        self._inner_logger = logging.getLogger(_INNER_EXPORTER_LOGGER)
        self._quiet_filter = _StreakFilter(self)

    @property
    def in_failure_streak(self) -> bool:
        """Whether export is currently failing. Read by the streak filter."""
        return self._failing

    def export(self, spans):
        from opentelemetry.sdk.trace.export import SpanExportResult

        try:
            result = self._inner.export([self._redact(span) for span in spans])
        except Exception as exc:  # noqa: BLE001 - fail open, always
            self._report(exc)
            return SpanExportResult.FAILURE

        if result is SpanExportResult.SUCCESS:
            self._end_streak()
        else:
            self._report("the receiver did not accept the batch")
        return result

    def shutdown(self):
        self._end_streak()
        return self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        return self._inner.force_flush(timeout_millis)

    def _redact(self, span):
        cleaned = _scrub_attributes(span.attributes, self._redact_content)
        events = _scrub_events(span.events, self._redact_content)
        status = _scrub_status(span.status, self._redact_content)
        if cleaned is None and events is None and status is None:
            return span

        from opentelemetry.sdk.trace import ReadableSpan

        return ReadableSpan(
            name=span.name,
            context=span.get_span_context(),
            parent=span.parent,
            resource=span.resource,
            attributes=span.attributes if cleaned is None else cleaned,
            events=span.events if events is None else events,
            links=span.links,
            kind=span.kind,
            status=span.status if status is None else status,
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )

    def _report(self, reason) -> None:
        if self._failing:
            logger.debug("OpenTelemetry span export still failing: %s", reason)
            return
        self._failing = True
        logger.warning(
            "OpenTelemetry span export failed and spans are being dropped: %s", reason
        )
        # Hold back the exporter's own per-attempt errors for the rest of the streak.
        # Without this, "one warning per failure streak" describes this module only,
        # while the log fills up from the logger underneath it.
        if self._quiet_filter not in self._inner_logger.filters:
            self._inner_logger.addFilter(self._quiet_filter)

    def _end_streak(self) -> None:
        self._failing = False
        if self._quiet_filter in self._inner_logger.filters:
            self._inner_logger.removeFilter(self._quiet_filter)


class _StreakFilter(logging.Filter):
    """Drop the inner exporter's records while its owner is in a failure streak.

    The owner is held weakly, and a filter whose owner is gone lets everything
    through. A logger's filter list is global and outlives the exporter that added
    it, so a strong reference here would let a discarded exporter silence the OTLP
    logger for the life of the process.
    """

    def __init__(self, owner):
        super().__init__()
        self._owner_ref = weakref.ref(owner)

    def filter(self, record):
        owner = self._owner_ref()
        return True if owner is None else not owner.in_failure_streak


def _clear_streak_filters() -> None:
    """Take every streak filter off the OTLP logger, whatever added it."""
    inner_logger = logging.getLogger(_INNER_EXPORTER_LOGGER)
    for existing in list(inner_logger.filters):
        if isinstance(existing, _StreakFilter):
            inner_logger.removeFilter(existing)


def _install_instrumentors(provider) -> list:
    """Install each global instrumentor, and let one failure cost only itself."""
    failures = []
    for name, module_path, class_name in _INSTRUMENTORS:
        try:
            module = importlib.import_module(module_path)
            instrumentor = getattr(module, class_name)()
            extra = _INSTRUMENTOR_KWARGS.get(name)
            instrumentor.instrument(
                tracer_provider=provider, **(extra() if extra else {})
            )
            _INSTALLED.append(instrumentor)
        except Exception as exc:  # noqa: BLE001 - fail open, per instrumentor
            failures.append(name)
            logger.warning(
                "OpenTelemetry instrumentor %r did not install, "
                "continuing without it: %s",
                name,
                exc,
            )
    return failures


def reset_telemetry() -> None:
    """Uninstrument everything this module installed and forget the status.

    Tests need it. A process that must re-read its environment needs it too. It
    does not reset OpenTelemetry's own global tracer provider, which has a
    set-once setter this module does not own.
    """
    global _STATUS
    with _LOCK:
        while _INSTALLED:
            instrumentor = _INSTALLED.pop()
            try:
                instrumentor.uninstrument()
            except Exception as exc:  # noqa: BLE001 - teardown must not raise
                logger.debug("Uninstrumenting %r failed: %s", instrumentor, exc)
        _clear_streak_filters()
        if _STATUS is not None and _STATUS.tracer_provider is not None:
            try:
                _STATUS.tracer_provider.shutdown()
            except Exception as exc:  # noqa: BLE001 - teardown must not raise
                logger.debug("Tracer provider shutdown failed: %s", exc)
        _STATUS = None
