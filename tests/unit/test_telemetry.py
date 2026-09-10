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
