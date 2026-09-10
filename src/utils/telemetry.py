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
import sys
import threading
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
    ("logging", "opentelemetry.instrumentation.logging", "LoggingInstrumentor"),
)

_LOGGING_INSTRUMENTOR = "logging"

# URL-valued attributes whose query string comes off before export, and attributes
# that hold nothing but a query string and so come off whole. The SSO redirect route
# receives an OAuth authorization code here, and the catalog search receives the
# user's own words. Neither is model content, so ARCHI_OTEL_CAPTURE_CONTENT does not
# reach them.
_URL_ATTRIBUTES = frozenset({"http.url", "url.full", "http.target", "url.path"})
_QUERY_ATTRIBUTES = frozenset({"url.query"})


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


def _scrub_attributes(attributes):
    """Return attributes with no query string, or None when nothing had one."""
    if not attributes:
        return None

    cleaned = {}
    changed = False
    for key, value in attributes.items():
        if key in _QUERY_ATTRIBUTES:
            changed = True
            continue
        if key in _URL_ATTRIBUTES and isinstance(value, str) and "?" in value:
            cleaned[key] = value.split("?", 1)[0]
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

    def __init__(self, inner):
        self._inner = inner
        self._failing = False

    def export(self, spans):
        from opentelemetry.sdk.trace.export import SpanExportResult

        try:
            result = self._inner.export([self._redact(span) for span in spans])
        except Exception as exc:  # noqa: BLE001 - fail open, always
            self._report(exc)
            return SpanExportResult.FAILURE

        if result is SpanExportResult.SUCCESS:
            self._failing = False
        else:
            self._report("the receiver did not accept the batch")
        return result

    def shutdown(self):
        return self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        return self._inner.force_flush(timeout_millis)

    def _redact(self, span):
        cleaned = _scrub_attributes(span.attributes)
        if cleaned is None:
            return span

        from opentelemetry.sdk.trace import ReadableSpan

        return ReadableSpan(
            name=span.name,
            context=span.get_span_context(),
            parent=span.parent,
            resource=span.resource,
            attributes=cleaned,
            events=span.events,
            links=span.links,
            kind=span.kind,
            status=span.status,
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


def _install_instrumentors(provider) -> list:
    """Install each global instrumentor, and let one failure cost only itself."""
    failures = []
    for name, module_path, class_name in _INSTRUMENTORS:
        try:
            module = importlib.import_module(module_path)
            instrumentor = getattr(module, class_name)()
            instrumentor.instrument(tracer_provider=provider)
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
        if _STATUS is not None and _STATUS.tracer_provider is not None:
            try:
                _STATUS.tracer_provider.shutdown()
            except Exception as exc:  # noqa: BLE001 - teardown must not raise
                logger.debug("Tracer provider shutdown failed: %s", exc)
        _STATUS = None
