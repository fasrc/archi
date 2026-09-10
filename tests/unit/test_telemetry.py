"""Guards for the default-off OpenTelemetry bootstrap.

Three properties matter more than the spans themselves, and each one is a way the
module can hurt a service that never asked for telemetry:

1. It is off unless an operator turns it on.
2. It never raises into ``setup_logging()``, which is the first statement of every
   service entrypoint.
3. It exports no prompt, no completion and no URL query string.
"""

import ast
import logging
import os
from pathlib import Path

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


class TestTheFlaskSeam:
    """One line per entrypoint, and the SSO route never reaches a span.

    The Flask instrumentor is applied to an application object rather than to the
    ``Flask`` class. Every entrypoint runs ``from flask import Flask`` at module
    import, so it holds the original class before this module runs, and a global
    patch of ``flask.Flask`` would never reach the object it builds.
    """

    def test_it_does_nothing_when_telemetry_is_off(self):
        from flask import Flask

        assert telemetry.instrument_flask_app(Flask("archi-test")) is False

    def test_a_request_is_traced_and_the_sso_route_is_not(self, monkeypatch):
        monkeypatch.setenv(ENABLE, "true")
        status = telemetry.init_telemetry()
        memory = _attach_memory_exporter(status.tracer_provider)
        app = _app_with_routes()

        assert telemetry.instrument_flask_app(app) is True

        client = app.test_client()
        client.get("/health?q=payroll")
        client.get("/redirect?code=s3cret&state=xyz")

        spans = memory.get_finished_spans()
        names = [span.name for span in spans]
        recorded = str([dict(span.attributes) for span in spans])

        assert any("/health" in name for name in names)
        assert not any("redirect" in name for name in names)
        assert "s3cret" not in recorded
        assert "payroll" not in recorded

    def test_it_fails_open_when_the_instrumentor_raises(self, monkeypatch, caplog):
        monkeypatch.setenv(ENABLE, "true")
        telemetry.init_telemetry()

        with caplog.at_level(logging.WARNING):
            instrumented = telemetry.instrument_flask_app(object())

        assert instrumented is False
        assert _warning_count(caplog) == 1


def _app_with_routes():
    from flask import Flask

    app = Flask("archi-test")

    @app.route("/health")
    def health():
        return "ok"

    @app.route("/redirect")
    def sso_callback():
        return "ok"

    return app


def _attach_memory_exporter(provider):
    """Read the spans a provider produces, through the same scrubber production uses."""
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    memory = InMemorySpanExporter()
    provider.add_span_processor(
        SimpleSpanProcessor(telemetry.RedactingSpanExporter(memory))
    )
    return memory


class TestLangChainCarriesTheContentConfig:
    """The seam that would otherwise export every prompt."""

    def test_the_instrumentor_receives_a_hiding_config(self, monkeypatch):
        monkeypatch.setenv(ENABLE, "true")
        monkeypatch.setattr(
            telemetry,
            "_INSTRUMENTORS",
            (("langchain", __name__, "_ConfigCapturingInstrumentor"),),
        )
        captured = {}
        monkeypatch.setattr(_ConfigCapturingInstrumentor, "captured", captured)

        telemetry.init_telemetry()

        assert captured["config"].hide_inputs is True
        assert captured["config"].hide_outputs is True

    def test_the_real_instrumentor_installs(self, monkeypatch):
        """The package is a dependency, so its absence is a broken install."""
        monkeypatch.setenv(ENABLE, "true")

        status = telemetry.init_telemetry()

        assert "langchain" not in status.failures


class _ConfigCapturingInstrumentor:
    captured: dict = {}

    def instrument(self, **kwargs):
        self.captured.update(kwargs)

    def uninstrument(self, **_kwargs):
        pass


class TestRetrievedDocumentsNeverLeaveTheProcess:
    """OpenInference hides model inputs and outputs. It does not hide documents.

    Measured on 2026-09-09 with openinference-instrumentation 0.1.62:
    ``TraceConfig(hide_inputs=True, hide_outputs=True).mask()`` returns
    ``retrieval.documents.0.document.content`` unchanged. Its mask table
    (``openinference/instrumentation/config.py:335-430``) has a case for reranker
    documents and none for retrieval documents, so a retriever span carries the
    text of every chunk the knowledge base returned.

    A promise in a document is not a control. The exporter enforces this one, which
    also means the guarantee does not depend on a table inside a dependency.
    """

    def test_document_content_is_redacted(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("retriever") as span:
            span.set_attribute(_document_key(0, "content"), "the grant proposal text")
            span.set_attribute(_document_key(1, "content"), "a second chunk")

        (exported,) = memory.get_finished_spans()

        assert exported.attributes[_document_key(0, "content")] == "__REDACTED__"
        assert exported.attributes[_document_key(1, "content")] == "__REDACTED__"
        assert "grant proposal" not in str(dict(exported.attributes))

    def test_document_metadata_is_redacted(self):
        """Metadata carries titles, paths and source URLs, which are content too."""
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("retriever") as span:
            span.set_attribute(_document_key(0, "metadata"), '{"title": "payroll"}')

        (exported,) = memory.get_finished_spans()

        assert exported.attributes[_document_key(0, "metadata")] == "__REDACTED__"

    def test_the_rest_of_the_document_attributes_survive(self):
        """Redaction must leave enough to tell that retrieval happened, and how well."""
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("retriever") as span:
            span.set_attribute(_document_key(0, "content"), "secret text")
            span.set_attribute(_document_key(0, "id"), "doc-42")
            span.set_attribute(_document_key(0, "score"), 0.87)

        (exported,) = memory.get_finished_spans()

        assert exported.attributes[_document_key(0, "id")] == "doc-42"
        assert exported.attributes[_document_key(0, "score")] == 0.87

    def test_reranker_documents_are_redacted_too(self):
        """The same leaf name appears under the reranker attributes."""
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("reranker") as span:
            span.set_attribute(
                "reranker.input_documents.0.document.content", "secret text"
            )

        (exported,) = memory.get_finished_spans()

        assert (
            exported.attributes["reranker.input_documents.0.document.content"]
            == "__REDACTED__"
        )

    def test_the_content_flag_releases_document_text(self, monkeypatch):
        """One flag, one meaning: it releases model content and retrieved content."""
        monkeypatch.setenv(CONTENT, "true")
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("retriever") as span:
            span.set_attribute(_document_key(0, "content"), "the grant proposal text")

        (exported,) = memory.get_finished_spans()

        assert exported.attributes[_document_key(0, "content")] == (
            "the grant proposal text"
        )

    def test_the_real_attribute_names_are_the_ones_guarded(self):
        """Bind the guard to the package's own constants, not to a guess."""
        from openinference.semconv.trace import DocumentAttributes, SpanAttributes

        key = (
            f"{SpanAttributes.RETRIEVAL_DOCUMENTS}.0."
            f"{DocumentAttributes.DOCUMENT_CONTENT}"
        )

        assert key == _document_key(0, "content")


def _document_key(index, leaf):
    return f"retrieval.documents.{index}.document.{leaf}"


class TestCredentialsInUrlPathsAreRedacted:
    """A webhook URL is the credential. There is nothing after the ``?`` to strip.

    ``src/interfaces/mattermost.py:59`` and ``src/interfaces/piazza.py:88`` read the
    whole URL from a secret and hand it to ``requests``. The requests instrumentor
    then records it in ``http.url``. Stripping the query string does nothing for
    these, because the token sits in the path.
    """

    def test_a_slack_webhook_url_loses_its_token(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("POST") as span:
            span.set_attribute(
                "http.url", "https://hooks.slack.com/services/T01/B02/Xy7SeCrEt"
            )

        (exported,) = memory.get_finished_spans()

        assert "Xy7SeCrEt" not in exported.attributes["http.url"]
        assert exported.attributes["http.url"].startswith("https://hooks.slack.com/")

    def test_a_mattermost_webhook_url_loses_its_token(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("POST") as span:
            span.set_attribute("url.full", "https://mm.example.edu/hooks/ab12cd34ef")

        (exported,) = memory.get_finished_spans()

        assert "ab12cd34ef" not in exported.attributes["url.full"]

    def test_an_ordinary_url_path_is_left_alone(self):
        """Redaction that eats every path would make the traces useless."""
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("GET") as span:
            span.set_attribute("http.url", "https://api.openai.com/v1/chat/completions")

        (exported,) = memory.get_finished_spans()

        assert (
            exported.attributes["http.url"]
            == "https://api.openai.com/v1/chat/completions"
        )


class TestExceptionsAndStatusAreScrubbedToo:
    """A span is not only its attributes.

    A failed ``requests`` call records an exception event and a status description,
    and both can quote the URL that failed. Scrubbing only ``span.attributes`` lets
    the same secret out through a different door.
    """

    def test_an_exception_event_loses_the_query_string(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("GET") as span:
            span.add_event(
                "exception",
                {
                    "exception.type": "ConnectionError",
                    "exception.message": (
                        "failed to reach https://archi.example/redirect?code=s3cret"
                    ),
                },
            )

        (exported,) = memory.get_finished_spans()
        (event,) = exported.events

        assert "s3cret" not in event.attributes["exception.message"]
        assert event.name == "exception"
        assert event.attributes["exception.type"] == "ConnectionError"

    def test_an_exception_event_loses_a_webhook_token(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("POST") as span:
            span.add_event(
                "exception",
                {
                    "exception.stacktrace": (
                        "POST https://hooks.slack.com/services/T01/B02/Xy7SeCrEt "
                        "raised ConnectionError"
                    )
                },
            )

        (exported,) = memory.get_finished_spans()
        (event,) = exported.events

        assert "Xy7SeCrEt" not in event.attributes["exception.stacktrace"]

    def test_the_status_description_loses_the_query_string(self):
        from opentelemetry.trace import Status, StatusCode

        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("GET") as span:
            span.set_status(
                Status(
                    StatusCode.ERROR,
                    "GET https://archi.example/redirect?code=s3cret failed",
                )
            )

        (exported,) = memory.get_finished_spans()

        assert "s3cret" not in exported.status.description
        assert exported.status.status_code.name == "ERROR"

    def test_a_span_with_a_clean_event_is_passed_through(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("agent step") as span:
            span.add_event("tool called", {"tool.name": "search"})

        (exported,) = memory.get_finished_spans()
        (event,) = exported.events

        assert event.attributes["tool.name"] == "search"


class TestTheInnerExporterDoesNotFloodTheLog:
    """One broken receiver, one warning. The claim has to cover both loggers.

    ``OTLPSpanExporter`` logs its own error on every retry of every batch. Reporting
    once from this wrapper while the exporter underneath logs every attempt is not
    the documented behaviour, it is the same flood with an extra line.
    """

    def test_the_inner_exporter_logger_is_quietened_during_a_streak(self, caplog):
        inner_logger = logging.getLogger(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        )
        exporter = telemetry.RedactingSpanExporter(_FailingExporter())

        with caplog.at_level(logging.ERROR):
            exporter.export([])
            inner_logger.error("Failed to export batch code: 000")
            inner_logger.error("Failed to export batch code: 000")

        from_inner = [r for r in caplog.records if r.name == inner_logger.name]

        assert from_inner == [], "the exporter's own errors must be held back too"

    def test_the_inner_logger_speaks_again_after_a_success(self, caplog):
        inner_logger = logging.getLogger(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        )
        inner = _FailingExporter()
        exporter = telemetry.RedactingSpanExporter(inner)

        with caplog.at_level(logging.ERROR):
            exporter.export([])
            inner.succeed = True
            exporter.export([])
            inner_logger.error("Failed to export batch code: 000")

        from_inner = [r for r in caplog.records if r.name == inner_logger.name]

        assert len(from_inner) == 1

    def test_shutdown_puts_the_inner_logger_back(self):
        inner_logger = logging.getLogger(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        )
        before = list(inner_logger.filters)

        exporter = telemetry.RedactingSpanExporter(_FailingExporter())
        exporter.export([])
        exporter.shutdown()

        assert list(inner_logger.filters) == before


class TestRelativeUrlsInExceptionTextAreScrubbed:
    """The standard requests failure does not quote an absolute URL.

    urllib3 raises ``Max retries exceeded with url: /redirect?code=…``. The path is
    relative, so a rule that matches only ``http://`` leaves the credential in place
    in exactly the message an operator is most likely to see.
    """

    def test_a_relative_target_loses_its_query_string(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("GET") as span:
            span.add_event(
                "note",
                {
                    "detail": (
                        "Max retries exceeded with url: /redirect?code=s3cret "
                        "(Caused by NewConnectionError)"
                    )
                },
            )

        (exported,) = memory.get_finished_spans()
        (event,) = exported.events

        assert "s3cret" not in event.attributes["detail"]
        assert "/redirect" in event.attributes["detail"]
        assert "NewConnectionError" in event.attributes["detail"]

    def test_a_relative_webhook_path_loses_its_token(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("POST") as span:
            span.add_event(
                "note",
                {
                    "detail": "Max retries exceeded with url: /services/T01/B02/Xy7SeCrEt"
                },
            )

        (exported,) = memory.get_finished_spans()
        (event,) = exported.events

        assert "Xy7SeCrEt" not in event.attributes["detail"]

    def test_ordinary_prose_with_a_slash_is_untouched(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("step") as span:
            span.add_event("note", {"detail": "retrying 2/3 for /v1/models"})

        (exported,) = memory.get_finished_spans()
        (event,) = exported.events

        assert event.attributes["detail"] == "retrying 2/3 for /v1/models"


class TestExceptionTextIsContentUntilProvenOtherwise:
    """An exception message can carry model output, and often does.

    ``src/archi/providers/huit_bedrock_provider.py:270`` puts the first 500
    characters of the upstream response body into a ``RuntimeError``. The global
    LangChain instrumentor records that string in the span's exception event and its
    status. ``hide_inputs`` and ``hide_outputs`` cover input and output attributes,
    not arbitrary exception text, so nothing else stops it.

    No rule can tell a message that quotes a model from one that does not, so on the
    default path the message and the stack trace do not leave the host. The service
    log still has both in full.
    """

    def test_the_exception_message_does_not_leave_by_default(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("llm") as span:
            span.add_event(
                "exception",
                {
                    "exception.type": "RuntimeError",
                    "exception.message": (
                        "HUIT Bedrock request failed: HTTP 500 — "
                        "the patient records show a diagnosis of"
                    ),
                    "exception.stacktrace": "Traceback… the same text again",
                },
            )

        (exported,) = memory.get_finished_spans()
        (event,) = exported.events

        assert event.attributes["exception.message"] == "__REDACTED__"
        assert event.attributes["exception.stacktrace"] == "__REDACTED__"
        assert event.attributes["exception.type"] == "RuntimeError"

    def test_the_status_description_does_not_leave_by_default(self):
        from opentelemetry.trace import Status, StatusCode

        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("llm") as span:
            span.set_status(
                Status(StatusCode.ERROR, "RuntimeError: the model said something")
            )

        (exported,) = memory.get_finished_spans()

        assert exported.status.description == "__REDACTED__"
        assert exported.status.status_code.name == "ERROR"

    def test_the_content_flag_restores_the_message_but_not_the_credential(
        self, monkeypatch
    ):
        monkeypatch.setenv(CONTENT, "true")
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("llm") as span:
            span.add_event(
                "exception",
                {
                    "exception.message": (
                        "posting to https://hooks.slack.com/services/T01/B02/Xy7SeCrEt "
                        "failed with the model output attached"
                    )
                },
            )

        (exported,) = memory.get_finished_spans()
        (event,) = exported.events
        message = event.attributes["exception.message"]

        assert "the model output attached" in message
        assert "Xy7SeCrEt" not in message


class TestTheFirstFailedBatchDoesNotLogTwiceOver:
    """The inner exporter logs during ``export()``, not after it.

    A filter installed once ``export()`` has returned is installed too late for the
    batch that just failed, and a test that logs through the inner logger afterwards
    cannot see the difference. This one logs from inside the exporter, which is where
    the real one logs.
    """

    def test_the_inner_logger_speaks_once_across_a_failure_streak(self, caplog):
        inner_name = "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        exporter = telemetry.RedactingSpanExporter(_LoggingFailingExporter())

        with caplog.at_level(logging.WARNING):
            exporter.export([])
            exporter.export([])
            exporter.export([])

        from_inner = [r for r in caplog.records if r.name == inner_name]
        from_archi = [r for r in caplog.records if r.name == telemetry.logger.name]

        assert len(from_inner) == 1, "the exporter must not log once per batch"
        assert len(from_archi) == 1


class _LoggingFailingExporter:
    """Fails, and logs on its own logger from inside export(), as the real one does."""

    def export(self, spans):
        logging.getLogger(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        ).error("Failed to export batch code: 000, reason: connection refused")
        return _span_export_result().FAILURE

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


class TestTheDatabaseStatementIsNotAContentChannel:
    """A SQL statement can carry the conversation, and one of archi's does.

    Most archi statements are parameterised — ``VALUES (%s, %s, %s)`` — and the
    values never reach the driver as statement text. ``SQL_INSERT_CONVO`` is the
    exception: ``psycopg2.extras.execute_values`` expands the rows into the
    statement before sending it, which is correct use of the driver and leaves the
    finished statement holding the question and the whole answer. The psycopg2
    instrumentor records that finished statement in ``db.statement``.

    Found on the deployed check, not in this file: every other content path had a
    test, and this one exported a user's question to the receiver anyway. The rule
    is therefore about the attribute, not about the call site — a statement is
    scrubbed whatever built it.

    Literals go and the shape stays. An operator still sees which table was
    written and by what kind of statement, which is most of what a database span
    is for.
    """

    STATEMENT = (
        "\nINSERT INTO conversations (\n"
        "    archi_service, conversation_id, sender, content, ts\n"
        ")\n"
        "VALUES ('Chatbot',1,'User','What is in the seed document?',"
        "'2026-09-10T12:27:20+00:00'::timestamptz),"
        "('Chatbot',1,'archi','The seed document is a test file.',"
        "'2026-09-10T12:27:21+00:00'::timestamptz)\n"
        "RETURNING message_id;\n"
    )

    def test_the_conversation_does_not_leave_inside_the_statement(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("archi-db") as span:
            span.set_attribute("db.statement", self.STATEMENT)
            span.set_attribute("db.system", "postgresql")

        (exported,) = memory.get_finished_spans()
        statement = exported.attributes["db.statement"]

        assert "What is in the seed document?" not in statement
        assert "The seed document is a test file." not in statement
        assert "Chatbot" not in statement

    def test_the_shape_of_the_statement_survives(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("archi-db") as span:
            span.set_attribute("db.statement", self.STATEMENT)

        (exported,) = memory.get_finished_spans()
        statement = exported.attributes["db.statement"]

        assert "INSERT INTO conversations" in statement
        assert "RETURNING message_id" in statement
        assert "archi_service, conversation_id, sender, content, ts" in statement

    def test_a_parameterised_statement_is_left_alone(self):
        """The common case has no literal in it, so nothing should change."""
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")
        parameterised = (
            "\nINSERT INTO timing (\n    message_id,\n    msg_duration\n)\n"
            "VALUES (%s, %s);\n"
        )

        with tracer.start_as_current_span("INSERT") as span:
            span.set_attribute("db.statement", parameterised)

        (exported,) = memory.get_finished_spans()

        assert exported.attributes["db.statement"] == parameterised

    def test_a_doubled_quote_inside_a_literal_does_not_end_it(self):
        """``'it''s'`` is one literal. A rule that stops at the second quote
        would leave the rest of the row in the clear."""
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("archi-db") as span:
            span.set_attribute(
                "db.statement",
                "INSERT INTO conversations VALUES ('it''s the patient record','x')",
            )

        (exported,) = memory.get_finished_spans()
        statement = exported.attributes["db.statement"]

        assert "patient record" not in statement
        assert "INSERT INTO conversations VALUES" in statement

    def test_an_escape_string_literal_is_covered_too(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("archi-db") as span:
            span.set_attribute(
                "db.statement",
                r"INSERT INTO conversations VALUES (E'line\'s secret text', 2)",
            )

        (exported,) = memory.get_finished_spans()
        statement = exported.attributes["db.statement"]

        assert "secret text" not in statement

    def test_a_dollar_quoted_body_is_covered_too(self):
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("archi-db") as span:
            span.set_attribute(
                "db.statement",
                "INSERT INTO conversations VALUES ($tag$the whole answer$tag$)",
            )

        (exported,) = memory.get_finished_spans()
        statement = exported.attributes["db.statement"]

        assert "the whole answer" not in statement

    def test_a_backslash_does_not_end_a_literal_early(self):
        """psycopg2 ends a literal by doubling the quote, never by escaping it.

        Measured against the driver on 2026-09-10, both server modes:

            standard_conforming_strings = on
              "it's a conversation"  -> 'it''s a conversation'
              "back\\slash"          -> 'back\\slash'
            standard_conforming_strings = off
              "back\\slash"          -> 'back\\\\slash'

        So a backslash inside a literal is data, and the closing quote is the first
        single quote after it. An escape-aware rule reading ``\\'`` would run past
        that quote and expose ``second value``.

        This is the one statement psycopg2 does emit that carries the ambiguous
        sequence, and it is why the escape-aware reading was refused. The exporter
        takes the whole statement here rather than guess.
        """
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")
        statement = "INSERT INTO t VALUES ('ends with a backslash \\', 'second value')"

        with tracer.start_as_current_span("INSERT") as span:
            span.set_attribute("db.statement", statement)

        (exported,) = memory.get_finished_spans()
        scrubbed = exported.attributes["db.statement"]

        assert "second value" not in scrubbed
        assert "ends with a backslash" not in scrubbed
        assert scrubbed == "__REDACTED__"

    def test_an_ambiguous_backslash_quote_takes_the_whole_statement(self):
        """``\\'`` reads two ways, and the exporter cannot tell which is meant.

        With ``standard_conforming_strings`` on, the literal ends at that quote and
        the backslash is the last character of the value. With it off, the backslash
        escapes the quote and the literal runs on. The exporter sees a string, not a
        session, so it cannot know which server wrote it.

        Reading it one way leaks under the other: an escape-aware rule run over
        psycopg2's own default-mode output for a value ending in a backslash walks
        past the real closing quote and exposes the next value. So when the two
        readings disagree, nothing goes.
        """
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("SELECT") as span:
            span.set_attribute(
                "db.statement",
                "SELECT 'secret\\' leaked', 'second conversation'",
            )

        (exported,) = memory.get_finished_spans()
        scrubbed = exported.attributes["db.statement"]

        assert "leaked" not in scrubbed
        assert "second conversation" not in scrubbed
        assert scrubbed == "__REDACTED__"

    def test_a_backslash_that_is_not_before_a_quote_keeps_the_shape(self):
        """The ambiguity is the sequence, not the character. A backslash sitting in
        the middle of a value reads the same either way, so the statement survives."""
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("INSERT") as span:
            span.set_attribute(
                "db.statement",
                "INSERT INTO t VALUES ('back\\slash and more', 'next')",
            )

        (exported,) = memory.get_finished_spans()

        assert exported.attributes["db.statement"] == "INSERT INTO t VALUES ('?', '?')"

    def test_a_long_pathological_statement_scrubs_in_bounded_time(self):
        """An unterminated dollar quote and an unterminated string, both long.

        A rewrite that runs on every exported span must not become the reason a
        service stalls.
        """
        import time

        statement = "SELECT $tag$" + ("a" * 40000) + " and '" + ("b" * 40000)

        start = time.monotonic()
        telemetry._scrub_statement(statement)
        elapsed = time.monotonic() - start

        assert elapsed < 1.0, f"scrubbing took {elapsed:.2f}s"

    def test_many_unmatched_dollar_tags_do_not_stall_the_export(self):
        """Each dollar opener sends the lazy body scan to the end of the string.

        With many distinct tags and no closer, that is quadratic: measured at about
        0.8 seconds for 324 KB and rising with the square. ``export()`` calls this
        on the batch processor's thread, so the cost is paid where spans are shipped.

        A statement that large has no shape worth reading, so the size budget takes
        it whole and the scan never starts.
        """
        import time

        statement = "SELECT " + " ".join(f"$t{i}x$" for i in range(40000))
        assert len(statement) > 300_000

        start = time.monotonic()
        scrubbed = telemetry._scrub_statement(statement)
        elapsed = time.monotonic() - start

        assert scrubbed == "__REDACTED__"
        assert elapsed < 0.05, f"scrubbing took {elapsed:.2f}s"

    @pytest.mark.parametrize(
        ("label", "statement"),
        [
            (
                "block comment",
                "SELECT 1 /* the patient asked about a diagnosis */ FROM t",
            ),
            ("line comment", "SELECT 1 FROM t -- the patient asked about a diagnosis"),
            (
                "quoted identifier",
                'SELECT "the patient asked about a diagnosis" FROM t',
            ),
        ],
    )
    def test_text_outside_a_literal_takes_the_whole_statement(self, label, statement):
        """A literal is not the only place text fits.

        A comment and a delimited identifier both hold arbitrary characters, and
        neither is a string literal, so the literal rule walks straight past them.
        These are constructs the rule does not model, and the answer to a construct
        it does not model is to keep the statement rather than to export what it
        did not read.
        """
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("SELECT") as span:
            span.set_attribute("db.statement", statement)

        (exported,) = memory.get_finished_spans()
        scrubbed = exported.attributes["db.statement"]

        assert "diagnosis" not in scrubbed, label
        assert scrubbed == "__REDACTED__", label

    def test_many_dollar_signs_under_the_budget_do_not_stall_the_export(self):
        """The size budget bounds the input, not the work.

        Each unmatched opener sends the lazy body scan to the end of the string, so
        thousands of them inside 32 KB still cost about half a second per span —
        measured at 0.59 seconds for 4000 tags in 35 KB. The count is what has to be
        bounded, not only the length.
        """
        import time

        statement = "SELECT " + " ".join(f"$t{i}x$" for i in range(3000))
        assert len(statement) < telemetry._STATEMENT_BUDGET

        start = time.monotonic()
        scrubbed = telemetry._scrub_statement(statement)
        elapsed = time.monotonic() - start

        assert scrubbed == "__REDACTED__"
        assert elapsed < 0.05, f"scrubbing took {elapsed:.2f}s"

    def test_a_statement_inside_the_budget_still_keeps_its_shape(self):
        """The budget must not swallow the statements this rule exists to keep."""
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")
        statement = (
            "INSERT INTO document_chunks (chunk_text) VALUES ('" + ("a" * 8000) + "')"
        )

        with tracer.start_as_current_span("archi-db") as span:
            span.set_attribute("db.statement", statement)

        (exported,) = memory.get_finished_spans()
        scrubbed = exported.attributes["db.statement"]

        assert scrubbed == "INSERT INTO document_chunks (chunk_text) VALUES ('?')"

    def test_the_legacy_string_mode_form_is_covered(self):
        """With ``standard_conforming_strings`` off psycopg2 doubles the backslash
        as well as the quote, so the same rule reads it correctly."""
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")
        statement = "INSERT INTO t VALUES ('both \\\\ and ''quote'' together', 'next')"

        with tracer.start_as_current_span("INSERT") as span:
            span.set_attribute("db.statement", statement)

        (exported,) = memory.get_finished_spans()
        scrubbed = exported.attributes["db.statement"]

        assert "quote" not in scrubbed
        assert "next" not in scrubbed
        assert scrubbed == "INSERT INTO t VALUES ('?', '?')"

    def test_a_dollar_tag_that_is_not_ascii_is_still_a_dollar_tag(self):
        """PostgreSQL tags follow unquoted-identifier rules, which are not ASCII.

        An identifier may hold any letter, not only ``[A-Za-z_]``, so ``$é$…$é$``
        is a valid dollar-quoted string. archi writes no dollar-quoted SQL today, so
        nothing reaches this path from this codebase — but that is the same argument
        that left ``db.statement`` unscrubbed until a deployment proved otherwise,
        and it is worth exactly as much here.
        """
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("archi-db") as span:
            span.set_attribute(
                "db.statement",
                "INSERT INTO conversations VALUES ($é$the whole answer$é$)",
            )

        (exported,) = memory.get_finished_spans()
        statement = exported.attributes["db.statement"]

        assert "the whole answer" not in statement
        assert "INSERT INTO conversations VALUES" in statement

    def test_a_numbered_placeholder_is_not_read_as_a_dollar_tag(self):
        """``$1`` is a bound parameter. A tag may not start with a digit, so the
        rule must not treat ``$1 … $1`` as a quoted body and eat the predicate."""
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")
        statement = "SELECT sender FROM conversations WHERE id = $1 AND role = $2"

        with tracer.start_as_current_span("SELECT") as span:
            span.set_attribute("db.statement", statement)

        (exported,) = memory.get_finished_spans()

        assert exported.attributes["db.statement"] == statement

    def test_the_new_semantic_convention_key_is_covered(self):
        """Newer instrumentation writes ``db.query.text`` for the same thing."""
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("archi-db") as span:
            span.set_attribute("db.query.text", self.STATEMENT)

        (exported,) = memory.get_finished_spans()

        assert "What is in the seed document?" not in (
            exported.attributes["db.query.text"]
        )

    def test_the_content_flag_restores_the_statement(self, monkeypatch):
        monkeypatch.setenv(CONTENT, "true")
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("archi-db") as span:
            span.set_attribute("db.statement", self.STATEMENT)

        (exported,) = memory.get_finished_spans()

        assert exported.attributes["db.statement"] == self.STATEMENT

    def test_an_embedding_vector_does_not_ride_along(self):
        """The ingest writes one chunk embedding per row, inline.

        Measured on the deployed check: the chunk insert carried an 8388 character
        attribute, nearly all of it one float array. An embedding is the chunk in
        another form, and the array costs every trace real bytes. The literal rule
        does not reach it, because a number is not a quoted string.
        """
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")
        statement = (
            "INSERT INTO document_chunks "
            "(document_id, chunk_index, chunk_text, embedding, metadata)\n"
            "VALUES (1, 0, 'the chunk text', ARRAY[ -0.044845741242170334,"
            "0.049786292016506195, -0.06653116643428802,0.03228151053190231])"
        )

        with tracer.start_as_current_span("archi-db") as span:
            span.set_attribute("db.statement", statement)

        (exported,) = memory.get_finished_spans()
        scrubbed = exported.attributes["db.statement"]

        assert "0.044845741242170334" not in scrubbed
        assert "the chunk text" not in scrubbed
        assert "INSERT INTO document_chunks" in scrubbed
        assert "ARRAY[/* 4 numbers */]" in scrubbed, (
            "the length is not content, and for an ingest failure the dimension is "
            "most of what the array was telling you"
        )

    def test_an_ordinary_number_is_left_alone(self):
        """A number that is not a vector is an identifier or a limit."""
        memory, provider = _recording_provider()
        tracer = provider.get_tracer("test")
        statement = "SELECT sender FROM conversations WHERE id = 42 LIMIT 5;"

        with tracer.start_as_current_span("SELECT") as span:
            span.set_attribute("db.statement", statement)

        (exported,) = memory.get_finished_spans()

        assert exported.attributes["db.statement"] == statement


TELEMETRY_HELPERS = frozenset({"init_telemetry", "instrument_flask_app"})


def _entrypoint_paths():
    root = Path(__file__).resolve().parents[2]
    return list((root / "src" / "bin").glob("service_*.py"))


def _telemetry_names_used(tree):
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id in TELEMETRY_HELPERS
    }


def _names_bound_by_imports(tree):
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound.add(alias.asname or alias.name)
    return bound


class TestEntrypointsImportWhatTheyCall:
    """A call site without its import is a NameError at start, not at import.

    ``py_compile`` accepts the file, the unit suite never imports these modules,
    and the service dies on the first deployment instead. This guard reads the
    entrypoints as source and checks the binding, so the class of mistake cannot
    reach a container again.
    """

    @pytest.mark.parametrize("path", sorted(_entrypoint_paths()))
    def test_a_telemetry_helper_is_imported_where_it_is_called(self, path):
        import ast

        tree = ast.parse(path.read_text())
        called = _telemetry_names_used(tree)
        if not called:
            pytest.skip(f"{path.name} calls no telemetry helper")

        missing = called - _names_bound_by_imports(tree)

        assert not missing, (
            f"{path.name} calls {sorted(missing)} without importing it. "
            "The module starts, reaches the call, and raises NameError."
        )
