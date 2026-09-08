# OpenTelemetry Readiness

**Author:** Austin Swinney, FASRC — Harvard University
**Date:** September 2026
**Status:** Assessment, not scheduled. Observability is parked under the
[release plan](release-plan-2026.md). This document records what the work needs so a
plan decision can rest on evidence.
**Code anchors:** verified against `dev` at `3170498c` (2026-09-08)

---

## TL;DR

- archi has no OpenTelemetry today. No package, import, or config key mentions it.
  Every grep hit for `otel`, `arize`, or `tracing` is the word "summarize" or archi's
  own `agent_traces` table.
- Six things are required, in this order: a plan decision, a version choice for the
  OpenTelemetry suite, dependencies in two files with one base-image rebuild, one
  default-off bootstrap module at five seams, a receiver that stores traces, and
  redaction as a code default.
- The base requirements pin `protobuf==4.25.8`. Two coherent choices exist. Pin the
  OpenTelemetry suite at 1.27.0, which accepts that protobuf. Or bump protobuf to 7.x
  and take the current 1.44.0 suite. An unpinned install picks neither: pip pairs the
  1.44 SDK with a December 2022 exporter.
- Recommendation: traces first, exporter endpoint from environment variables, default
  off, fail open, content hidden unless a second flag turns it on. Metrics and a
  collector come later.

---

## 1. Current state

### 1.1 Logging

`setup_logging()` in `src/utils/logging.py:23-36` calls `logging.basicConfig` with
`force=True` and a plain text format string (`src/utils/logging.py:26`). There is no
JSON formatter, no request ID, no trace ID, and no `extra=` structured field anywhere
in `src/`. Every service entrypoint calls this function first.

### 1.2 Web tier

Three processes build a Flask app: chat (`src/bin/service_chat.py:41`), grader
(`src/bin/service_grader.py:25`), and data-manager (`src/bin/service_data_manager.py:189`,
which mounts the uploader app). The chat app also registers blueprints for the REST
API, the OpenAI-compatible `/v1` routes, service alerts, and the evaluation console.
All three run on the Flask development server through `app.run`
(`src/bin/service_chat.py:47-52`). There is no gunicorn and no uvicorn, so there is
no pre-fork hook problem. Containers start the script directly
(`src/cli/templates/dockerfiles/Dockerfile-chat:49`).

The chat stream runs the agent in a one-worker `ThreadPoolExecutor`
(`src/interfaces/chat_app/app.py:2254`). OpenTelemetry context does not cross that
boundary on its own. Section 2.4 covers the fix.

### 1.3 LLM and agent

LangChain is the abstraction for every provider in `src/archi/providers/`:
`ChatOpenAI` (OpenAI, OpenRouter, CERN LiteLLM, vLLM), `ChatAnthropic`,
`ChatGoogleGenerativeAI` (`gemini_provider.py:80-97`), `ChatOllama`
(`local_provider.py:87-107`), and the hand-rolled `HuitBedrockChat`, a
`BaseChatModel` subclass that calls the HUIT proxy with `requests`
(`src/archi/providers/huit_bedrock_provider.py:262-270`). `LocalProvider` also probes
model lists with `urllib.request` (`local_provider.py:153-161` and `220-231`).

The agent loop in `src/archi/pipelines/agents/base_react.py` is LangGraph. Its
`invoke` accepts LangChain callback handlers (`base_react.py:401-419`). It already
computes a per-step `duration_ms` (`base_react.py:611-623`) and normalizes token usage
(`base_react.py:231-259`).

### 1.4 Database and HTTP clients

psycopg2 is the only database driver. Most access goes through `ConnectionPool`
(`src/utils/connection_pool.py:36`). 11 modules import `requests` directly. One module
uses `httpx` directly, and the LangChain OpenAI, Anthropic, Gemini, and Ollama clients
use it internally. `urllib.request` appears in `local_provider.py` and
`src/bin/service_benchmark.py:1091`.

### 1.5 Timing data already stored

The `timing`, `agent_traces`, and `agent_tool_calls` tables
(`src/cli/templates/init.sql:474-539`) store a trace ID, per-message timestamps,
per-step tool calls, token totals, and `total_duration_ms`. This is a hand-rolled span
store. Grafana reads Postgres directly (`src/cli/templates/grafana/datasources.yaml:5`).
There is no metrics emission and no `/metrics` endpoint.

### 1.6 Plan status

Every current observability issue is parked: #258 and #227 (a correlation ID for the
`hybrid_search` fallback warning), #204 and #193 (client timing columns). See the
parked table in the release plan. This is also why the Phoenix evaluation platform
lives outside this repo.

---

## 2. Requirements

### 2.1 A plan decision

Under the release plan, work enters a milestone only if a release feature is broken,
wrong, or dishonest without it. OpenTelemetry does not meet that bar today. It needs
one of two decisions: file it as `parked` with this document as the reason, or a human
names the feature it gates.

### 2.2 Choose the suite version

OpenTelemetry releases the SDK, the exporter, and the instrumentors in lockstep. The
base requirements pin `protobuf==4.25.8` (`requirements/requirements-base.txt:81`),
and OTLP exporters from 1.28 onward need protobuf 5 or newer. Pip dry runs against
the base constraints (method in Appendix A) gave these results:

| Requested suite | Result with `protobuf==4.25.8` |
|---|---|
| unpinned (latest) | SDK 1.44.0 paired with exporter **1.15.0** (Dec 2022): an untested pair |
| 1.25.0 / 0.46b0 | no resolution |
| **1.27.0 / 0.48b0** | **resolves**: SDK, exporter, and proto all 1.27.0 |
| 1.29.0 / 0.50b0 | no resolution |
| 1.33.0 / 0.54b0 | no resolution |
| latest, pin removed | SDK, exporter, and proto all 1.44.0; protobuf 7.36.1 |

Two coherent choices:

- **Option A. Pin the suite at 1.27.0 / 0.48b0.** No protobuf change. The suite is
  from September 2024. The OTLP wire format is stable, so any current receiver accepts
  it. Newer GenAI semantic conventions are absent, which matters only if archi emits
  those attributes by hand.
- **Option B. Bump protobuf and take 1.44.0 / 0.65b0.** Only `onnxruntime`
  constrains protobuf in the base set (`>=4.25.8`), and it accepts 7.x. The
  `pulsar-client` bound `<=3.20.3` applies only to extras archi does not install. This
  is resolver evidence, not runtime evidence. A three-major-version protobuf change
  reaches every service image and must pass the preview deploy and the smoke suite
  before it lands.

Either way the suite must be pinned. Never leave it unpinned.

### 2.3 Declare dependencies in two files

Service images run `pip install .` on top of the base image. A package must be in both
`pyproject.toml` and `requirements/requirements-base.txt` with the same pin.
`pyproject.toml:38-58` records this rule. Any edit to `requirements-base.txt` triggers
a base-image rebuild (`.github/workflows/publish-base-images.yml:43`), so one rebuild
is unavoidable under either option in 2.2. After the rebuild,
`scripts/dev/update_service_base_images.py` bumps the digest pin in 15 service
Dockerfiles (`src/cli/templates/dockerfiles/Dockerfile-chat:2-3` and its siblings).

Packages, all at one suite version:

- `opentelemetry-sdk`
- `opentelemetry-exporter-otlp-proto-http`. HTTP, not gRPC: no grpcio wheel, and
  Phoenix accepts it.
- `opentelemetry-instrumentation-flask`
- `opentelemetry-instrumentation-requests`
- `opentelemetry-instrumentation-httpx`
- `opentelemetry-instrumentation-urllib`, for the `LocalProvider` model probes.
- `opentelemetry-instrumentation-psycopg2`
- `opentelemetry-instrumentation-threading`, so the request context reaches the agent
  worker thread.
- `opentelemetry-instrumentation-logging`
- `openinference-instrumentation-langchain`, if Phoenix is the receiver. Phoenix
  classifies spans by the `openinference.span.kind` attribute (LLM, TOOL, RETRIEVER,
  CHAIN). Plain OpenTelemetry spans show as unknown kind.

### 2.4 One bootstrap module at five seams

Add one module, `src/utils/telemetry.py`, with one function
`init_telemetry(service_name)`.

| Seam | Anchor | What it gives |
|---|---|---|
| `setup_logging()` | `src/utils/logging.py:23-36` | one init point for all 9 `src/bin/service_*.py` processes; the format string gains trace-ID placeholders here |
| Flask apps | `service_chat.py:41`, `service_grader.py:25`, `service_data_manager.py:189` | one server span per request |
| Agent worker thread | `src/interfaces/chat_app/app.py:2254` | the threading instrumentor carries the request context into the stream worker |
| LangChain callbacks | `base_react.py:401-419` | LLM, tool, and graph spans with no edit inside the 2460-line file |
| `ConnectionPool` | `src/utils/connection_pool.py:36` | database spans; the instrumentor must run before the pool is created |

Rules:

- **Default off.** The module returns at once unless `OTEL_EXPORTER_OTLP_ENDPOINT` is
  set or an archi flag enables it.
- **Fail open.** An import error or an exporter error must log one warning and leave
  the service up. The vLLM metrics shim (`docs/docs/fasrc_archi.md:70-82`) shows a
  metrics middleware that returned HTTP 500 on every request. Export failures must be
  visible in the service log, because the batch processor drops spans silently.
- **Config by environment variable.** The SDK reads `OTEL_SERVICE_NAME` and
  `OTEL_EXPORTER_OTLP_ENDPOINT` at process start, before the Postgres-seeded config
  loads. The compose template already passes an `env_file` and a per-service
  `environment` map (`src/cli/templates/base-compose.yaml:53-59`).
- **Content hidden by default.** See 2.7. A second, separate flag is the only way to
  export prompts, completions, or documents.
- **Trace ID in log lines.** The logging instrumentor adds `otelTraceID` and
  `otelSpanID` to each log record. It does not change archi's format string, and its
  own `basicConfig` call is a no-op because `setup_logging()` already installed a
  handler with `force=True`. So `setup_logging()` must select a format string with
  `%(otelTraceID)s` and `%(otelSpanID)s` when telemetry is on, and the plain string
  when it is off, or the plain path raises `KeyError` on every record. The
  `hybrid_search` fallback warning fires inside the worker thread, so the threading
  instrumentor is also required before the ID appears there. This work can close #258
  and #227, but only after a captured-log test inside an active span proves it.

### 2.5 Instrumentation coverage

| Traffic | Library | Instrumentor | Note |
|---|---|---|---|
| Inbound HTTP | Flask | flask | 3 app processes; exclude the SSO routes, see 2.7 |
| Agent worker | `ThreadPoolExecutor` | threading | context propagation, `app.py:2254` |
| LLM calls | LangChain: `ChatOpenAI`, `ChatAnthropic`, `ChatGoogleGenerativeAI`, `ChatOllama`, `HuitBedrockChat` | openinference-langchain, or a callback handler | all five are `BaseChatModel`, so callbacks cover them |
| LLM egress | httpx inside langchain-openai, langchain-anthropic, langchain-google-genai, langchain-ollama | httpx | |
| HUIT Bedrock, scrapers, Mattermost, Piazza | requests | requests | 11 modules |
| Model discovery | urllib.request | urllib | `local_provider.py`, benchmark harness |
| Postgres | psycopg2 | psycopg2 | pooled and direct calls |
| Logs | stdlib logging | logging | trace ID injection; OTLP log export is optional |
| Metrics | none today | none | needs a MeterProvider and a receiver; see 2.6 |

### 2.6 A receiver that stores traces

Nothing in this repo accepts or stores OTLP today. A collector alone is not enough: it
receives and forwards, and this stack has no Tempo, Jaeger, or Prometheus behind it.
Three options:

- **Phoenix as an archi compose service.** Copy the Grafana pattern: a compose block
  (`src/cli/templates/base-compose.yaml:285-320`), a `ServiceDefinition`
  (`src/cli/service_registry.py:115-128`), and the render wiring in
  `src/cli/managers/templates_manager.py`. Phoenix stores to its own Postgres or a
  SQLite volume and accepts OTLP over HTTP on port 6006. This is the only option that
  works on the FASRC dev GPU host without a network decision.
- **Phoenix on the claw workstation.** A media-composarr OpenSpec change, not yet
  applied, plans Phoenix with OTLP on a LAN address only. This document cannot verify
  that topology. Before the claw archi stack points at it, record the applied
  revision and a connectivity probe from the archi host.
- **A collector sidecar.** Only when more than one backend must receive the same
  spans. It adds a hop and a config file, and it stores nothing.

Whichever receiver is chosen, the bootstrap module must log export failures, and the
proposal for that receiver must name its transport security and its retention.

### 2.7 Redaction as a code default

The OpenInference LangChain instrumentor records prompts, completions, tool arguments,
and retrieved documents by default. Issue #258 excludes query text from logs on
purpose, and the same rule must hold for spans. The bootstrap module must:

- Construct the OpenInference `TraceConfig` with `hide_inputs=True` and
  `hide_outputs=True`. Environment advice is not enough; one missed variable exports
  everything.
- Expose one separate flag, for example `ARCHI_OTEL_CAPTURE_CONTENT=true`, as the only
  way to export content, and document it as a privacy decision.
- Exclude the SSO routes from Flask spans. The OAuth callback
  (`src/interfaces/chat_app/app.py:3457-3464`) receives the authorization code in the
  query string, and the catalog search (`src/interfaces/uploader_app/app.py:559-562`)
  receives the user query in `?q=`. HTTP semantic conventions keep the query string
  unless a span processor strips it, so add one that drops query strings from every
  URL attribute.
- Leave psycopg2 parameter capture off, its default. SQL text without parameters is
  acceptable; parameters are not.

### 2.8 Tests and the gate

- Add `tests/unit/test_telemetry.py`, flat like the other `src/utils` tests. Use
  `monkeypatch.setenv` (pattern: `tests/unit/test_fasrc_docs_agent.py:109`).
- Cover the off path, the fail-open path, and the on path with an in-memory span
  exporter.
- Assert spans for: one Flask request; the agent `invoke` and stream paths; one
  `HuitBedrockChat` call; one pooled psycopg2 call; and one captured log line with a
  trace ID, emitted from a worker thread inside an active span.
- Assert redaction: no prompt, document text, query string, or OAuth code appears in
  any exported span with the default flags.
- `src/interfaces/chat_app/app.py` is not imported by unit tests. Keep the call site
  there to one line, per `CLAUDE.md`.
- Do not edit `base_react.py` for spans. The callbacks parameter is enough.

---

## 3. Recommendation

Phase the work. Each phase is one PR.

1. **Dependencies.** Pick option A or B from 2.2. Add the pinned packages to both
   files, rebuild the base image once, bump 15 digests. Under option B this PR also
   bumps protobuf. Validate through the preview deploy and the smoke suite before
   merge. No OpenTelemetry code yet.
2. **Traces.** Bootstrap module, Flask, threading, requests, httpx, urllib, psycopg2,
   LangChain callbacks, log format. Default off. Content hidden. Exporter endpoint
   from environment. Redaction tests green.
3. **Receiver.** Phoenix as a compose service behind a flag like `grafana_enabled`.
   Point the claw stack at its own Phoenix only after a recorded probe.
4. **Metrics.** MeterProvider, request and LLM histograms, Prometheus, Grafana
   datasource. Only if someone asks for a metric the `timing` table cannot answer.

The `timing` and `agent_traces` tables map almost one to one onto the OpenTelemetry
GenAI semantic conventions. A span exporter can replace them later instead of a
parallel store. Do not remove them in phase 2.

---

## 4. Open decisions

- Does OpenTelemetry gate a release feature, or is it `parked`? Plan owner.
- Option A (suite 1.27.0, protobuf unchanged) or option B (protobuf 7.x, suite
  1.44.0)? Owner of the base image.
- Phoenix inside the archi compose, or on claw, or both? Depends on which host runs
  traces first.
- Do prompts and completions ever leave the host? Privacy owner. Default no.
- Does upstream `archi-physics/archi` want the bootstrap module? Upstream has no
  observability code and no open issue on this topic as of 2026-09-08. A default-off
  module is a low-conflict contribution.

---

## Appendix A. Dependency resolution method

Pip dry runs on 2026-09-08 with pip from the conda `archi` env (Python 3.11.15):

1. Copy `requirements/requirements-base.txt` at `dev` `3170498c`. Strip comments and
   extras so pip accepts it as a constraints file.
2. Run `pip install --dry-run --ignore-installed --report out.json -c <constraints>`
   with the packages in section 2.3, first unpinned, then at each suite version in the
   2.2 table.
3. Repeat the unpinned run with the `protobuf` line removed.
4. Resolve the whole base set with `protobuf` free. protobuf lands at 7.36.1. The only
   non-extra constraint is `onnxruntime==1.29.0 requires protobuf>=4.25.8`.

A dry run proves that the resolver is satisfied. It does not prove that the packages
work together at runtime. The base image installs the requirements file as-is
(`src/cli/templates/dockerfiles/base-python-image/Dockerfile:11`), so the pin, not the
conda env, is what the images get.

## Appendix B. False positives

A repo-wide grep for
`opentelemetry|otel|prometheus|langfuse|langsmith|phoenix|sentry|tracing|traceloop|arize`
hits `src/cli/managers/base_image_preflight.py:612` and
`src/interfaces/chat_app/app.py:1364`. Both are the word "summarize". The Prometheus
mentions in `docs/docs/fasrc_archi.md` describe vLLM's metrics middleware, not archi.
