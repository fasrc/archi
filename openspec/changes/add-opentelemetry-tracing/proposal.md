# Add default-off OpenTelemetry tracing

## Why

`docs/docs/proposals/opentelemetry-readiness.md` (PR #446, merged) records what archi needs
before it can emit OpenTelemetry data. It measured the tree and left four decisions open.
This change closes three of them and builds phases 1 and 2 of its recommendation.

archi emits no OpenTelemetry today. The `timing` and `agent_traces` tables hold span-shaped
data, but only archi reads them, and only Grafana renders them. A request that crosses the
chat app, the agent loop, an LLM provider and Postgres leaves no single record that ties the
parts together. Issues #258 and #227 ask for exactly that tie: a correlation ID on the
`hybrid_search` fallback warning. A trace ID in every log line answers both.

### The decisions this change closes

**Suite version: option A.** The readiness document measured two coherent choices. Option A
pins the OpenTelemetry suite at 1.27.0 / 0.48b0 and leaves `protobuf==4.25.8`
(`requirements/requirements-base.txt:81`) alone. Option B bumps protobuf to 7.x and takes the
1.44.0 suite. This change takes option A. A three-major-version protobuf bump reaches all 15
service images, and it is a separate risk from tracing. Option A carries that risk in no
image.

A probe on 2026-09-09 confirms the pin is safe in both directions. In a clean virtual
environment, `opentelemetry-exporter-otlp-proto-http==1.27.0` installs protobuf 4.25.9 and
imports. The same exporter still imports after protobuf is forced to 7.35.1, which is the
version the local conda `archi` environment carries. So the pinned suite works at the pinned
protobuf that CI and the images use, and it also works in a developer environment that has
drifted forward.

**LangChain seam: the global instrumentor.** Section 2.8 of the readiness document left this
open. `stream()` (`src/archi/pipelines/agents/base_react.py:518-557`) and `astream()`
(`:897`) accept no `callbacks` argument and forward none, so a callback handler alone
instruments the QA evaluator and leaves streamed chat with no LLM, tool or graph spans.
The two ways forward were a global `LangChainInstrumentor` or new callback forwarding in
`base_react.py`. This change takes the global instrumentor. It covers `invoke`, `stream` and
`astream` at one seam, and it needs no edit to the agent loop.

**Receiver: none yet.** Phase 3 of the readiness document chooses a receiver. That decision
depends on which host runs traces first, and it is not closed. This change reads the
endpoint from `OTEL_EXPORTER_OTLP_ENDPOINT` and stays off when the variable is absent.

The fourth decision, whether prompts and completions ever leave the host, stays with the
privacy owner. The default here is no, and one separate flag is the only way to change it.

## What Changes

- **Dependencies in four files.** Ten pinned packages enter `pyproject.toml` and
  `requirements/requirements-base.txt`. `scripts/dev/build_docker_images.sh:80-86`
  regenerates the two base-image `requirements.txt` files from the second of those, and
  `tests/unit/test_requirements_generated_in_sync.py` compares them byte for byte, so this
  change commits four files, not two.
- **One bootstrap module.** `src/utils/telemetry.py` holds `init_telemetry()`. It is off
  unless the operator turns it on. It fails open. It never raises into a caller.
- **Trace IDs in the log.** `setup_logging()` selects a trace-aware format string, but only
  after the logging instrumentor installs the record fields. If the instrumentor fails, the
  plain format stays.
- **One Flask seam per app.** The three Flask processes each call one helper. The SSO
  redirect route is excluded from spans.
- **A service name per process.** Each service block in `src/cli/templates/base-compose.yaml`
  renders its own `OTEL_SERVICE_NAME`. A process started outside compose derives its name
  from the entrypoint script.
- **Redaction as a code default.** The OpenInference `TraceConfig` hides inputs and outputs.
  A span exporter wrapper drops the query string from every URL attribute.

## Impact

- Affected specs: `observability` (new capability).
- Affected code: `src/utils/telemetry.py` (new), `src/utils/logging.py`,
  `src/bin/service_chat.py`, `src/bin/service_grader.py`,
  `src/bin/service_data_manager.py`, `src/cli/templates/base-compose.yaml`,
  `pyproject.toml`, `requirements/requirements-base.txt`, and the two generated
  base-image requirements files.
- **Runtime behaviour with no operator action: unchanged.** `init_telemetry()` reads two
  environment variables and returns. It starts no exporter, patches no library, and changes
  no log line.
- **One base-image rebuild.** `.github/workflows/publish-base-images.yml` rebuilds on the
  merge to `dev`, because `requirements/requirements-base.txt` changed. After the rebuild,
  `scripts/dev/update_service_base_images.py` bumps the digest pin in 15 service
  Dockerfiles. That bump is a follow-up commit on `dev`, not part of this change.
- **Not in this change.** No receiver, no metrics, and no removal of the `timing` or
  `agent_traces` tables. Both tables have live consumers: the chat history route
  (`src/interfaces/chat_app/app.py:5257-5300`) and the shipped Grafana dashboard.
- **Deployed validation.** `AGENTS.md:61-63` requires an end-to-end check against a running
  deployment. Unit spans do not satisfy it. The check is recorded in `tasks.md` and must run
  before merge.
