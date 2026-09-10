# Observability

archi can emit OpenTelemetry traces. The feature is off until you turn it on, and
archi ships no receiver, so you must point it at one you run yourself.

Read `docs/docs/proposals/opentelemetry-readiness.md` for the measurements behind the
design, and `openspec/changes/add-opentelemetry-tracing/` for what this change built.

## Turn tracing on

Set one environment variable on a service and restart it:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix.example:6006
```

The address is OTLP over HTTP. The exporter appends `/v1/traces` itself. With the
variable absent, archi reads it, finds nothing, and returns. No OpenTelemetry package
is imported and no log line changes.

Two more variables control the feature:

| Variable | Default | What it does |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | The receiver address. Setting it turns tracing on. |
| `ARCHI_OTEL_ENABLED` | unset | A true value turns tracing on with no endpoint, which records spans and exports none. A false value turns tracing **off** even when an endpoint is set. |
| `ARCHI_OTEL_CAPTURE_CONTENT` | unset | A true value puts prompts, completions and retrieved documents into spans. Read the privacy section below before you set it. |

`ARCHI_OTEL_ENABLED=false` is a kill switch. The services share one `.env` file, so an
endpoint there reaches every service. Setting the flag to false in one service's
`environment` block takes that service out without an edit to the shared file.

## What gets traced

| Traffic | How |
|---|---|
| Inbound HTTP | Flask, one server span per request, on the chat, grader and data-manager apps |
| LLM and agent calls | The OpenInference LangChain instrumentor, which covers `invoke`, `stream` and `astream` and gives Phoenix its span kinds |
| Outbound HTTP | `requests`, `httpx` and `urllib` |
| Databases | psycopg2 and sqlite3 |
| Logs | Each log line gains the trace ID and span ID of the span it was written in |

The agent runs in a worker thread. The chat stream copies the caller's context and
enters that thread through `contextvars.copy_context().run`, so the request span is
already active there. No threading instrumentor is involved.

## Service names

Each service block in the compose file renders its own `OTEL_SERVICE_NAME`, so the
receiver can tell the processes apart: `archi-chat`, `archi-data-manager`,
`archi-grader`, `archi-piazza`, `archi-mattermost`, `archi-redmine`, `archi-mailbox`,
`archi-benchmark` and `archi-config-seed`.

A process that compose did not start derives its name from the entrypoint script, so
`src/bin/service_chat.py` reports `archi-chat`. Anything that is not an entrypoint
reports `archi`. Set `OTEL_SERVICE_NAME` yourself to override either.

## Privacy

**Spans carry no prompt, no completion, no retrieved document and no URL query
string by default.** Four separate rules produce that result:

1. The OpenInference config hides model inputs and outputs. Each field is named in
   code rather than left to an environment variable, because one variable an
   operator misses exports every prompt.
2. Retrieved document text and document metadata are replaced with `__REDACTED__`
   before export. This is a separate rule because the OpenInference config does not
   cover it: its mask table handles reranker documents and leaves
   `retrieval.documents.0.document.content` untouched, so a retriever span would
   otherwise carry the text of every chunk the knowledge base returned. archi
   enforces this at the exporter, which also keeps the guarantee independent of a
   table inside a dependency.
3. Every URL attribute loses its query string before export, on every span.
4. The SSO redirect route is excluded from spans entirely. It receives an OAuth
   authorization code, and a request that carries a credential is better off with no
   span than with one whose safety rests on a scrubber.

`ARCHI_OTEL_CAPTURE_CONTENT=true` reverses rules 1 and 2. It never reverses rules 3
and 4, because an authorization code is not model content and no flag releases one.
Treat turning it on as a privacy decision with an owner, not a debugging
convenience.

Document identifiers and retrieval scores survive redaction. You can still see that
retrieval happened, how many chunks came back and how they ranked, without seeing
what they said.

Database spans carry the SQL text without its parameters. That is the psycopg2
instrumentor's default and archi does not change it.

## Failure behaviour

Telemetry never stops a service. A missing package, a failed instrumentor or a
bootstrap error logs one warning and leaves the service running with no tracing.

An unreachable receiver is a different failure, and it happens later. The batch
processor exports on its own thread and drops spans in silence when export fails, so
archi logs one warning per run of failures. One broken receiver produces one warning,
not one per batch.

If the logging instrumentor fails, the log keeps its plain format. The trace-aware
format reads record fields that only that instrumentor installs, so archi selects it
after the instrumentor succeeds and never before.

## Receivers

archi ships no receiver and no collector. Phoenix accepts OTLP over HTTP on port 6006
and classifies archi's spans by kind, which is why the OpenInference instrumentor is
a dependency. Any OTLP receiver works for plain traces.

Choosing where the receiver runs is a deployment decision that this change does not
make. Record the address, the transport security and the retention wherever you make
it.
