"""``setup_logging()`` must pick its format from what happened, not from intent.

The trace-aware format reads ``%(otelTraceID)s`` and ``%(otelSpanID)s``. Only the
logging instrumentor installs those record fields. A format chosen because telemetry
was *requested* leaves those placeholders in a handler whose records never carry
them, and then every record dies in formatting -- starting with the warning that
reports the failure. So the order is a fail-open requirement: instrument first, then
choose the format from the result.
"""

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.utils import telemetry
from src.utils.logging import setup_logging

ENABLE = "ARCHI_OTEL_ENABLED"
ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
SERVICE = "OTEL_SERVICE_NAME"


@pytest.fixture(autouse=True)
def clean_telemetry(monkeypatch):
    for var in (ENABLE, ENDPOINT, SERVICE, "VERBOSITY"):
        monkeypatch.delenv(var, raising=False)
    telemetry.reset_telemetry()
    _reset_global_tracer_provider()
    yield
    telemetry.reset_telemetry()
    _reset_global_tracer_provider()
    logging.basicConfig(format=telemetry.PLAIN_LOG_FORMAT, force=True)


def _reset_global_tracer_provider():
    from opentelemetry import trace

    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE._done = False


def _root_format():
    return logging.getLogger().handlers[0].formatter._fmt


def _hex_trace_id(span):
    return format(span.get_span_context().trace_id, "032x")


class TestTheFormatFollowsTheInstrumentor:
    def test_telemetry_off_keeps_the_plain_format(self):
        setup_logging()

        assert _root_format() == telemetry.PLAIN_LOG_FORMAT
        assert "otelTraceID" not in _root_format()

    def test_telemetry_on_selects_the_trace_aware_format(self, monkeypatch):
        monkeypatch.setenv(ENABLE, "true")

        setup_logging()

        assert _root_format() == telemetry.TRACE_LOG_FORMAT

    def test_a_failed_logging_instrumentor_keeps_the_plain_format(self, monkeypatch):
        """The case that would otherwise silence the log that reports the failure."""
        monkeypatch.setenv(ENABLE, "true")
        monkeypatch.setattr(
            telemetry,
            "_INSTRUMENTORS",
            (("logging", "archi_no_such_instrumentation_module", "Nope"),),
        )

        setup_logging()
        logging.getLogger("archi.test").warning("a later record still renders")

        assert _root_format() == telemetry.PLAIN_LOG_FORMAT

    def test_a_record_logged_in_a_span_carries_the_trace_id(self, monkeypatch, capsys):
        monkeypatch.setenv(ENABLE, "true")
        setup_logging()
        status = telemetry.init_telemetry()
        tracer = status.tracer_provider.get_tracer("test")

        with tracer.start_as_current_span("request") as span:
            logging.getLogger("archi.test").warning("hybrid_search fell back")
            expected = _hex_trace_id(span)

        rendered = capsys.readouterr().err

        assert "hybrid_search fell back" in rendered
        assert f"trace_id={expected}" in rendered


class TestTheAgentWorkerThreadAlreadyCarriesTheContext:
    """A pin, not a fix. No production code belongs in this test.

    The chat stream runs the agent in a one-worker pool, but it never hands the
    pool a bare callable. It snapshots the caller's context with
    ``contextvars.copy_context()`` and advances the generator through ``ctx.run``
    (``src/interfaces/chat_app/app.py:2245,2264``). OpenTelemetry keeps the active
    span in a ``ContextVar``, so the request span is already active in that worker
    and no threading instrumentor is needed. The contrast case below is what makes
    this test worth having: it shows the same pool losing the trace ID when the
    context is not copied.
    """

    def test_a_worker_entered_through_ctx_run_logs_the_trace_id(
        self, monkeypatch, capsys
    ):
        monkeypatch.setenv(ENABLE, "true")
        setup_logging()
        status = telemetry.init_telemetry()
        tracer = status.tracer_provider.get_tracer("test")

        with tracer.start_as_current_span("request") as span:
            expected = _hex_trace_id(span)
            context = contextvars.copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(context.run, _log_from_worker).result()

        assert f"trace_id={expected}" in capsys.readouterr().err

    def test_a_worker_without_the_copied_context_loses_it(self, monkeypatch, capsys):
        monkeypatch.setenv(ENABLE, "true")
        setup_logging()
        status = telemetry.init_telemetry()
        tracer = status.tracer_provider.get_tracer("test")

        with tracer.start_as_current_span("request") as span:
            expected = _hex_trace_id(span)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(_log_from_worker).result()

        rendered = capsys.readouterr().err

        assert f"trace_id={expected}" not in rendered
        assert "trace_id=0" in rendered


def _log_from_worker():
    logging.getLogger("archi.worker").warning("a warning from the agent worker")
