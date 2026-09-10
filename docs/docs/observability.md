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

Each long-running service block in the compose file renders its own
`OTEL_SERVICE_NAME`, so the receiver can tell the processes apart: `archi-chat`,
`archi-data-manager`, `archi-grader`, `archi-piazza`, `archi-mattermost`,
`archi-redmine`, `archi-mailbox` and `archi-benchmark`.

The `config-seed` block gets no name. It runs archi Python and talks to Postgres, but
that module never calls `setup_logging()`, so nothing in it starts a tracer. A name
there would advertise a traced service that emits nothing.

A process that compose did not start derives its name from the entrypoint script, so
`src/bin/service_chat.py` reports `archi-chat`. Anything that is not an entrypoint
reports `archi`. Set `OTEL_SERVICE_NAME` yourself to override either.

## Privacy

**Spans carry no prompt, no completion, no retrieved document and no credential by
default.** Seven separate rules produce that result:

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
3. Every URL loses its query string before export. This applies to URL attributes
   and to URLs quoted inside exception messages, stack traces and status
   descriptions, because a failed request names the URL it could not reach.
4. A URL whose path is itself a credential is cut at the credential. archi posts to
   two webhooks whose entire URL comes from a secret, Mattermost and Slack, and for
   those there is nothing after the `?` to strip. The host and the marker segment
   survive; the token does not. Ordinary paths are untouched.
5. Exception messages and stack traces do not leave the host. An exception can
   quote model output, and one in this codebase does: the HUIT Bedrock provider puts
   500 characters of the upstream response body into its error. Nothing can tell that
   message from an ordinary one, so on the default path both fields, and the span's
   status description, are replaced with `__REDACTED__`. The exception type and the
   error status code survive, and the service log still holds the full text.
6. The SSO redirect route is excluded from spans entirely. It receives an OAuth
   authorization code, and a request that carries a credential is better off with no
   span than with one whose safety rests on a scrubber.
7. Every string literal in a SQL statement is replaced before export. Nearly every
   archi statement is parameterised, so its text is only shape and this rule changes
   nothing. One statement is not: the insert that writes a conversation goes through
   `psycopg2.extras.execute_values`, which expands the rows into the statement before
   sending it, so the finished text holds the question and the whole answer. The
   table, the columns and the kind of statement survive. A bare number survives too,
   because it is an identifier or a limit and never the conversation, but an array of
   nothing but numbers does not: the ingest writes each chunk embedding inline, and
   an embedding is the chunk in another form. One chunk insert measured 8388
   characters, nearly all of it that one array. The length survives — the statement
   reads `ARRAY[/* 384 numbers */]` — because a count is not content and the
   dimension is most of what the array told you. A statement holding a backslash
   directly before a quote is dropped whole: that sequence ends a literal on a
   server with `standard_conforming_strings` on and continues one with it off, the
   exporter is handed a string rather than a session, and either reading leaks under
   the other. So is a statement over 32 KB, which is four times the largest one the
   deployed check produced and past the size where reading the shape helps anyone.

`ARCHI_OTEL_CAPTURE_CONTENT=true` reverses rules 1, 2, 5 and 7. It never reverses
rules 3, 4 and 6, because a credential is not model content and no flag releases one: an
exception message restored by that flag still loses the credentials in any URL it
quotes.
Treat turning it on as a privacy decision with an owner, not a debugging
convenience.

Document identifiers and retrieval scores survive redaction. You can still see that
retrieval happened, how many chunks came back and how they ranked, without seeing
what they said.

Database spans carry the shape of the statement, not the data in it. An earlier
version of this page said the psycopg2 instrumentor already gave that for free. The
deployed check refuted it: one streamed chat request exported a user's question and
archi's whole reply inside `db.statement`. Rule 7 exists because of that measurement.

**What rule 7 costs you.** Every literal goes, not only the ones carrying a
conversation. A predicate reads `status = '?'`, so a trace alone will not tell you
which status a query filtered on, and the same is true of interval values, regular
expressions and JSON paths written inline. That is deliberate: no rule can tell a
literal that holds a status from one that holds an answer, and archi would rather
lose the status. Two ways to get the value back — set
`ARCHI_OTEL_CAPTURE_CONTENT=true` for a bounded investigation, or read the row in the
database, which is where it actually lives.

## Known gaps

**Streamed `/v1/chat/completions` requests do not produce one trace.**
`_streaming_response()` returns its generator without `stream_with_context`, so Flask
ends the request, and the server span, before the generator runs. The agent spans for
that request become unparented roots and their log lines carry a different trace ID.
The chat UI path does not have this problem: it wraps its generator. Tracked as
issue #454, and not fixed here because it changes the request-context lifetime of a
user-facing endpoint.

**config-seed is not traced.** See the service names section above.

## Failure behaviour

Telemetry never stops a service, and a failure costs only what it has to.

A bootstrap error logs one warning and leaves the service running with no tracing at
all. A single instrumentor that is missing or raises is narrower: it logs one warning
naming itself, and the other instrumentors still install. Tracing stays on, the
exporter stays attached, and the traces are missing that one kind of span. So a
warning about, say, the psycopg2 instrumentor means database spans are gone, not that
tracing stopped. `init_telemetry()` reports which ones failed, and a failure of the
logging instrumentor is the one that also changes the log format back.

An unreachable receiver is a different failure, and it happens later. The batch
processor exports on its own thread and drops spans in silence when export fails, so
something has to say so.

The first failure of a streak produces two lines: the exporter's own error, which
names the endpoint and the HTTP code, and one archi warning. Every later failure in
the same streak produces nothing. archi holds the exporter's logger back for the
length of the streak and lets it speak again after the next successful export, so a
receiver that stays down costs two lines rather than two lines per batch. The
exporter's line is kept rather than suppressed because it carries the detail needed
to fix the problem.

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
