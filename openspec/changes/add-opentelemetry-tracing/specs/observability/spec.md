## ADDED Requirements

### Requirement: Telemetry is off until an operator turns it on

archi SHALL emit no OpenTelemetry data unless the operator sets `OTEL_EXPORTER_OTLP_ENDPOINT`
or sets the archi flag `ARCHI_OTEL_ENABLED` to a true value, and `ARCHI_OTEL_ENABLED` set to a
false value SHALL keep telemetry off even when an endpoint is present.

The default matters more than the feature. Nine service entrypoints call `setup_logging()`
first, and `init_telemetry()` runs inside it, so every archi process reaches this code on
every start. A default-on bootstrap would send spans from a benchmark harness, a CLI command
and a unit test run, to an endpoint nobody chose.

The explicit false value is a kill switch. An operator who inherits a stack with an endpoint
in the shared `.env` can stop the export for one service without an edit to that file.

#### Scenario: No endpoint and no flag

- **WHEN** a process calls `init_telemetry()` with neither `OTEL_EXPORTER_OTLP_ENDPOINT` nor `ARCHI_OTEL_ENABLED` set
- **THEN** the call returns a disabled status
- **AND** no instrumentor is installed
- **AND** no exporter is created

#### Scenario: The flag overrides a present endpoint

- **WHEN** `OTEL_EXPORTER_OTLP_ENDPOINT` is set and `ARCHI_OTEL_ENABLED` is `false`
- **THEN** the call returns a disabled status

#### Scenario: A second call does not instrument twice

- **WHEN** `init_telemetry()` runs twice in one process
- **THEN** the second call returns the status of the first
- **AND** it installs no instrumentor a second time

### Requirement: Telemetry failure never stops a service

A failure inside `init_telemetry()` SHALL log one warning and return a disabled status, and it
SHALL NOT raise into the caller.

`setup_logging()` is the first statement of every service entrypoint. An exception there stops
the service before it opens a port. The readiness document records the precedent: a vLLM
metrics middleware returned HTTP 500 on every request
(`docs/docs/fasrc_archi.md:70-82`).

Two failure shapes exist, and they surface at different times. An import error or an
instrumentor error happens at init. An export error happens later, on the batch processor's
own thread, and the processor drops the spans in silence. Both must reach the service log.

#### Scenario: An instrumentor raises at init

- **WHEN** telemetry is enabled and an instrumentor raises during `init_telemetry()`
- **THEN** the call returns without raising
- **AND** the service log holds one warning that names the failure

#### Scenario: The exporter cannot reach the receiver

- **WHEN** telemetry is enabled, the endpoint refuses connections, and a span is flushed
- **THEN** the process keeps running
- **AND** the service log holds a warning about the export

### Requirement: The log format changes only after the record fields exist

`setup_logging()` SHALL select a log format that reads `%(otelTraceID)s` and `%(otelSpanID)s`
only after the logging instrumentor installs those record fields, and it SHALL keep the plain
format in every other case.

The order is a fail-open requirement, not a preference. A format chosen from the *intent* to
enable telemetry leaves `%(otelTraceID)s` in a handler whose records never carry that field.
Every later record then dies in formatting, including the one warning that the fail-open
requirement above demands. The failure would silence the log that reports it.

#### Scenario: Telemetry is off

- **WHEN** telemetry is off and a service logs a line
- **THEN** the line renders in the plain format
- **AND** it holds no trace ID placeholder

#### Scenario: The logging instrumentor fails but telemetry is on

- **WHEN** telemetry is enabled and the logging instrumentor raises
- **THEN** a later log record still renders
- **AND** it renders in the plain format

#### Scenario: A log line inside an active span carries the trace ID

- **WHEN** telemetry is on and code logs a line inside an active span
- **THEN** the rendered line holds the trace ID of that span

### Requirement: Every process reports its own service name

A traced process SHALL report a `service.name` resource attribute that identifies that
process, taken from `OTEL_SERVICE_NAME` when the operator sets it, else from the argument to
`init_telemetry()`, else derived from the entrypoint script name.

The services share one `.env` file, so that file cannot carry a per-service name. Each service
block in `src/cli/templates/base-compose.yaml` has its own `environment` map, and each one
renders a distinct `OTEL_SERVICE_NAME`. The derived fallback covers a process that compose did
not start, such as the benchmark harness or a shell.

#### Scenario: The environment names the service

- **WHEN** `OTEL_SERVICE_NAME` is `archi-chat` and telemetry starts
- **THEN** the resource attribute `service.name` is `archi-chat`

#### Scenario: Nothing names the service

- **WHEN** no `OTEL_SERVICE_NAME` is set, no argument is given, and the entrypoint is `src/bin/service_chat.py`
- **THEN** the resource attribute `service.name` is `archi-chat`

### Requirement: Spans carry no prompt, completion, document or query string

A span SHALL carry no prompt text, completion text, retrieved document text, or URL query
string unless the operator sets `ARCHI_OTEL_CAPTURE_CONTENT` to a true value.

The OpenInference LangChain instrumentor records prompts, completions, tool arguments and
retrieved documents by default. Issue #258 excludes query text from logs on purpose, and a
span store is not a weaker place than a log file.

Query strings carry secrets that no flag should release. The SSO redirect route
(`src/interfaces/chat_app/app.py:3321,3457-3464`) receives an OAuth authorization code in the
query string, and the catalog search (`src/interfaces/uploader_app/app.py:559-562`) receives
the user's query in `?q=`. Two defences apply: the SSO route is excluded from Flask spans, and
a span exporter wrapper drops the query string from every URL attribute of every span.

The content flag does not reach the query string. `ARCHI_OTEL_CAPTURE_CONTENT` releases model
inputs and outputs. It never releases an authorization code.

#### Scenario: Default flags on a traced LLM call

- **WHEN** telemetry is on with default flags and an agent runs one LLM call
- **THEN** no exported span holds the prompt text
- **AND** no exported span holds the completion text

#### Scenario: A URL attribute carries a query string

- **WHEN** a span carries a URL attribute whose value holds a query string
- **THEN** the exported span carries that URL without the query string

#### Scenario: The SSO redirect route

- **WHEN** telemetry is on and a request reaches the SSO redirect route
- **THEN** the Flask instrumentor records no span for that request
