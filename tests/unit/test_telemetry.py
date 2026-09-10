"""Guards for the default-off OpenTelemetry bootstrap.

Three properties matter more than the spans themselves, and each one is a way the
module can hurt a service that never asked for telemetry:

1. It is off unless an operator turns it on.
2. It never raises into ``setup_logging()``, which is the first statement of every
   service entrypoint.
3. It exports no prompt, no completion and no URL query string.
"""

import logging
import os

import pytest

from src.utils import telemetry

ENABLE = "ARCHI_OTEL_ENABLED"
ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
SERVICE = "OTEL_SERVICE_NAME"
CONTENT = "ARCHI_OTEL_CAPTURE_CONTENT"

ALL_VARS = (ENABLE, ENDPOINT, SERVICE, CONTENT, "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")


@pytest.fixture(autouse=True)
def clean_telemetry(monkeypatch):
    """Give every test a process that has never initialised telemetry.

    ``reset_telemetry()`` clears archi's own state and uninstruments whatever the
    previous test installed. The global tracer provider is OpenTelemetry's, not
    archi's, and its public setter refuses a second call for the life of the
    process — so a test that wants a fresh provider has to clear the private slot
    the setter guards. That is contained here, in the fixture, and never in
    ``src/``.
    """
    for var in ALL_VARS:
        monkeypatch.delenv(var, raising=False)
    telemetry.reset_telemetry()
    _reset_global_tracer_provider()
    yield
    telemetry.reset_telemetry()
    _reset_global_tracer_provider()


def _reset_global_tracer_provider():
    from opentelemetry import trace

    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE._done = False


class TestTelemetryIsOffByDefault:
    """A process that nobody configured must reach no OpenTelemetry code."""

    def test_no_environment_means_disabled(self):
        status = telemetry.init_telemetry()

        assert status.enabled is False
        assert status.log_correlation is False
        assert status.tracer_provider is None

    def test_endpoint_alone_enables(self, monkeypatch):
        monkeypatch.setenv(ENDPOINT, "http://127.0.0.1:6006")

        assert telemetry.init_telemetry().enabled is True

    def test_flag_alone_enables(self, monkeypatch):
        monkeypatch.setenv(ENABLE, "true")

        assert telemetry.init_telemetry().enabled is True

    @pytest.mark.parametrize("value", ["0", "false", "False", "no", "off"])
    def test_false_flag_beats_a_present_endpoint(self, monkeypatch, value):
        """The kill switch: one service opts out of a shared .env endpoint."""
        monkeypatch.setenv(ENDPOINT, "http://127.0.0.1:6006")
        monkeypatch.setenv(ENABLE, value)

        assert telemetry.init_telemetry().enabled is False

    def test_second_call_returns_the_first_status(self, monkeypatch):
        monkeypatch.setenv(ENABLE, "true")
        first = telemetry.init_telemetry()

        monkeypatch.delenv(ENABLE)
        second = telemetry.init_telemetry()

        assert second is first


class TestServiceName:
    """Every process must be able to say which process it is."""

    def test_environment_wins(self, monkeypatch):
        monkeypatch.setenv(ENABLE, "true")
        monkeypatch.setenv(SERVICE, "archi-chat")

        status = telemetry.init_telemetry(service_name="ignored")

        assert status.service_name == "archi-chat"
        assert _resource_service_name(status) == "archi-chat"

    def test_argument_comes_next(self, monkeypatch):
        monkeypatch.setenv(ENABLE, "true")

        status = telemetry.init_telemetry(service_name="archi-grader")

        assert status.service_name == "archi-grader"
        assert _resource_service_name(status) == "archi-grader"

    @pytest.mark.parametrize(
        ("argv0", "expected"),
        [
            ("src/bin/service_chat.py", "archi-chat"),
            ("/opt/archi/src/bin/service_data_manager.py", "archi-data-manager"),
            ("src/bin/service_grader.py", "archi-grader"),
            ("pytest", "archi"),
            ("", "archi"),
        ],
    )
    def test_entrypoint_fallback(self, monkeypatch, argv0, expected):
        """A process compose did not start still names itself."""
        monkeypatch.setattr(telemetry.sys, "argv", [argv0])

        assert telemetry.resolve_service_name() == expected


class TestFailOpenAtInit:
    """setup_logging() is the first statement of every entrypoint."""

    def test_a_broken_instrumentor_does_not_stop_the_others(self, monkeypatch, caplog):
        installed = []
        monkeypatch.setenv(ENABLE, "true")
        monkeypatch.setattr(
            telemetry,
            "_INSTRUMENTORS",
            (
                ("missing", "archi_no_such_instrumentation_module", "Nope"),
                ("recorder", __name__, "_RecordingInstrumentor"),
            ),
        )
        monkeypatch.setattr(_RecordingInstrumentor, "installed", installed)

        with caplog.at_level(logging.WARNING):
            status = telemetry.init_telemetry()

        assert status.enabled is True
        assert "missing" in status.failures
        assert installed == ["recorder"]
        assert any("missing" in record.getMessage() for record in caplog.records)

    def test_a_failure_in_the_bootstrap_returns_disabled_and_does_not_raise(
        self, monkeypatch, caplog
    ):
        monkeypatch.setenv(ENABLE, "true")

        def explode(*_args, **_kwargs):
            raise RuntimeError("provider is unavailable")

        monkeypatch.setattr(telemetry, "_build_tracer_provider", explode)

        with caplog.at_level(logging.WARNING):
            status = telemetry.init_telemetry()

        assert status.enabled is False
        assert any(
            "provider is unavailable" in record.getMessage()
            for record in caplog.records
        )

    def test_the_logging_instrumentor_decides_the_log_format(self, monkeypatch):
        """A failed logging instrumentor must leave the plain format in place."""
        monkeypatch.setenv(ENABLE, "true")
        monkeypatch.setattr(
            telemetry,
            "_INSTRUMENTORS",
            (("logging", "archi_no_such_instrumentation_module", "Nope"),),
        )

        status = telemetry.init_telemetry()

        assert status.enabled is True
        assert status.log_correlation is False
        assert status.log_format == telemetry.PLAIN_LOG_FORMAT
        assert "%(otelTraceID)s" not in status.log_format


class _RecordingInstrumentor:
    """Stands in for a real instrumentor so a test installs nothing global."""

    installed: list = []

    def instrument(self, **_kwargs):
        self.installed.append("recorder")

    def uninstrument(self, **_kwargs):
        pass


def _resource_service_name(status):
    return status.tracer_provider.resource.attributes["service.name"]


class TestQueryStringsNeverLeaveTheProcess:
    """A URL attribute is the one place a secret arrives without anyone deciding.

    The SSO redirect route receives an OAuth authorization code in the query
    string, and the catalog search receives the user's own words in ``?q=``.
    Neither is model content, so the content flag must not reach them. They come
    off every span, always.
    """

    def test_query_strings_come_off_url_attributes(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("GET /redirect") as span:
            span.set_attribute(
                "http.url", "https://archi.example/redirect?code=s3cret&state=xyz"
            )
            span.set_attribute("url.full", "https://archi.example/search?q=payroll")
            span.set_attribute("http.target", "/redirect?code=s3cret")
            span.set_attribute("url.query", "code=s3cret")
            span.set_attribute("http.method", "GET")

        (exported,) = memory.get_finished_spans()
        attributes = exported.attributes

        assert attributes["http.url"] == "https://archi.example/redirect"
        assert attributes["url.full"] == "https://archi.example/search"
        assert attributes["http.target"] == "/redirect"
        assert "url.query" not in attributes
        assert "s3cret" not in str(dict(attributes))
        assert "payroll" not in str(dict(attributes))

    def test_the_rest_of_the_span_survives_the_scrub(self):
        """Rebuilding a span must not quietly drop what a reader needs."""
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("GET /redirect") as span:
            span.set_attribute("http.url", "https://archi.example/redirect?code=s3cret")
            span.set_attribute("http.status_code", 302)
            span.add_event("redirected")

        (exported,) = memory.get_finished_spans()

        assert exported.name == "GET /redirect"
        assert exported.attributes["http.status_code"] == 302
        assert [event.name for event in exported.events] == ["redirected"]
        assert exported.resource is not None
        assert exported.start_time is not None
        assert exported.end_time is not None
        assert exported.instrumentation_scope.name == "test"

    def test_a_span_with_no_url_attribute_is_passed_through(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("agent step") as span:
            span.set_attribute("archi.step", 3)

        (exported,) = memory.get_finished_spans()

        assert exported.attributes["archi.step"] == 3


class TestExportFailuresReachTheLog:
    """The batch processor drops spans in silence. Someone has to say so.

    An export failure happens on the processor's own thread, long after
    ``init_telemetry()`` returned, so the init guard cannot stand in for this.
    """

    def test_a_failing_exporter_is_reported_and_does_not_raise(self, caplog):
        exporter = telemetry.RedactingSpanExporter(_FailingExporter())

        with caplog.at_level(logging.WARNING):
            result = exporter.export([])

        assert result is _span_export_result().FAILURE
        assert _warning_count(caplog) == 1

    def test_a_raising_exporter_is_reported_and_does_not_raise(self, caplog):
        exporter = telemetry.RedactingSpanExporter(_RaisingExporter())

        with caplog.at_level(logging.WARNING):
            result = exporter.export([])

        assert result is _span_export_result().FAILURE
        assert _warning_count(caplog) == 1

    def test_one_warning_per_failure_streak(self, caplog):
        inner = _FailingExporter()
        exporter = telemetry.RedactingSpanExporter(inner)

        with caplog.at_level(logging.WARNING):
            exporter.export([])
            exporter.export([])
            exporter.export([])

        assert _warning_count(caplog) == 1, "a broken receiver must not flood the log"

    def test_a_new_streak_warns_again(self, caplog):
        inner = _FailingExporter()
        exporter = telemetry.RedactingSpanExporter(inner)

        with caplog.at_level(logging.WARNING):
            exporter.export([])
            inner.succeed = True
            exporter.export([])
            inner.succeed = False
            exporter.export([])

        assert _warning_count(caplog) == 2

    def test_an_unreachable_receiver_leaves_the_process_running(self, caplog):
        """The end-to-end shape: a real exporter, a real batch processor.

        Port 1 on the loopback interface refuses connections, and the exporter
        retries under its own one-second deadline before it gives up.
        """
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()
        exporter = telemetry.RedactingSpanExporter(
            OTLPSpanExporter(endpoint="http://127.0.0.1:1/v1/traces", timeout=1)
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        tracer = provider.get_tracer("test")

        with caplog.at_level(logging.WARNING):
            with tracer.start_as_current_span("doomed"):
                pass
            provider.force_flush(5000)

        assert _warning_count(caplog) >= 1
        assert tracer.start_span("still alive") is not None
        provider.shutdown()


class TestContentIsHiddenByDefault:
    """OpenInference records prompts, completions and documents unless told not to."""

    def test_the_default_config_hides_inputs_and_outputs(self):
        config = telemetry.build_trace_config()

        assert config.hide_inputs is True
        assert config.hide_outputs is True
        assert config.hide_input_text is True
        assert config.hide_output_text is True

    def test_the_flag_is_the_only_way_to_export_content(self, monkeypatch):
        monkeypatch.setenv(CONTENT, "true")

        config = telemetry.build_trace_config()

        assert config.hide_inputs is False
        assert config.hide_outputs is False

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "maybe"])
    def test_anything_but_a_true_value_keeps_content_hidden(self, monkeypatch, value):
        monkeypatch.setenv(CONTENT, value)

        assert telemetry.build_trace_config().hide_inputs is True


def _recording_provider():
    """A provider whose exporter is the scrubber in front of an in-memory sink."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        SimpleSpanProcessor(telemetry.RedactingSpanExporter(memory))
    )
    return memory, provider


def _span_export_result():
    from opentelemetry.sdk.trace.export import SpanExportResult

    return SpanExportResult


def _warning_count(caplog):
    return sum(
        1
        for record in caplog.records
        if record.levelno >= logging.WARNING and record.name == telemetry.logger.name
    )


class _FailingExporter:
    succeed = False

    def export(self, spans):
        result = _span_export_result()
        return result.SUCCESS if self.succeed else result.FAILURE

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


class _RaisingExporter:
    def export(self, spans):
        raise ConnectionError("connection refused")

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


class TestExporterWiring:
    """Recording spans and shipping them are separate decisions."""

    def test_an_endpoint_installs_an_exporting_processor(self, monkeypatch):
        monkeypatch.setenv(ENDPOINT, "http://127.0.0.1:6006")

        status = telemetry.init_telemetry()

        assert status.enabled is True
        assert status.exporting is True

    def test_the_flag_without_an_endpoint_records_but_ships_nothing(self, monkeypatch):
        """No endpoint means no receiver, and a span with nowhere to go is not an error."""
        monkeypatch.setenv(ENABLE, "true")

        status = telemetry.init_telemetry()

        assert status.enabled is True
        assert status.exporting is False
