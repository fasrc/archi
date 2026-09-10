# Add default-off OpenTelemetry tracing

## Why

### Where the decision to do this came from

The release plan parks every observability issue, and `release-plan-2026.md:213` still
lists #258 and #227 in the parked table. That table is not overridden here. The rule
it enforces is that **automation** never schedules parked work, and that an issue
leaves parked only when a human decides it does. A human asked for this work
directly, on 2026-09-09, naming the merged readiness proposal as the thing to build.
That request is the decision the rule asks for.

What this change deliberately does not do:

- It closes no parked issue. #258, #227, #204 and #193 stay exactly where they are.
- It moves nothing into a milestone and re-triages nothing.
- It removes nothing from the parked table.

Trace-ID correlation in the log is a capability this work happens to deliver, and it
is what #258 and #227 describe. Whether that capability lets those issues close is a
plan decision with an owner, and it is not made here.


`docs/docs/proposals/opentelemetry-readiness.md` (PR #446, merged) records what archi needs
before it can emit OpenTelemetry data. It measured the tree and left four decisions open.
This change closes three of them and builds phases 1 and 2 of its recommendation.

archi emits no OpenTelemetry today. The `timing` and `agent_traces` tables hold span-shaped
data, but only archi reads them, and only Grafana renders them. A request that crosses the
chat app, the agent loop, an LLM provider and Postgres leaves no single record that ties the
parts together.

**On #258 and #227.** Those two issues ask for a correlation ID on the `hybrid_search`
fallback warning, and a trace ID in every log line would subsume what they ask for. They are
**parked** — `docs/docs/proposals/release-plan-2026.md:213` rows them as
"observability duplicate pair, diagnosability not correctness" — and this change does not
un-park them, does not schedule them, and must not be read as implementing them. Raised in
review, and the earlier wording here did read that way; corrected.

The distinction is not cosmetic. Under the pinned release plan an issue leaves `parked` only
when a human decides it gates a feature release, never as a side effect of other work. So:
this change is driven by the readiness assessment in PR #446, the two issues keep their
`parked` label, and whether to close them once a trace ID ships is a human call made on the
issues themselves. Nothing here touches the `hybrid_search` warning's call site.

This change also carries no milestone, and the release plan schedules no OpenTelemetry work.
It lands as an operator decision to merge rather than as a milestone gate — recorded in
`tasks.md` under "Scheduling" so the next reader does not have to infer it.

### The decisions this change closes

**Suite version: option B, and option A is not installable.** The readiness document
measured two choices with a pip dry run. Option A pins the suite at 1.27.0 / 0.48b0 and keeps
`protobuf==4.25.8`. Option B bumps protobuf and takes the current suite. It recommended
option A as the lower-risk one, and it warned that a dry run proves only that the resolver is
satisfied, never that the packages work together at runtime. That warning was the right one.

A runtime probe on 2026-09-09 refutes option A. `opentelemetry-instrumentation==0.48b0`
imports `pkg_resources` at module scope (`dependencies.py:4`), and setuptools 82 removed that
module. The package declares only `setuptools>=16.0`, so a fresh install takes the newest
setuptools, and every instrumentor import then raises `ModuleNotFoundError`. Measured in the
conda `archi` environment, which carries setuptools 82.0.1, and again in a clean virtual
environment, which resolved setuptools 84.0.0. Under option A, telemetry would fail open on
every start and emit nothing, forever.

No middle version exists. `opentelemetry-proto` needs protobuf 5 or newer from 1.28 onward, so
1.27.0 is the last core release that accepts protobuf 4. `opentelemetry-instrumentation`
dropped `pkg_resources` in 0.50b0, which pairs with core 1.29.0. The two ranges do not
overlap, so a working OTLP exporter today requires protobuf 5 or newer.

So this change takes option B: core packages at 1.44.0, instrumentation packages at 0.65b0,
and `protobuf` from 4.25.8 to 7.36.1. The same probe installed all nine instrumentors plus
`openinference-instrumentation-langchain==0.1.74` under setuptools 84 and imported every one.

The protobuf bump is the cost, and it is real. It reaches all 15 service images. Only one
package in the base set constrains protobuf: `onnxruntime`, which `flashrank` pulls in, and
which asks for `protobuf>=4.25.8` with no upper bound. `pulsar-client==3.5.0` bounds protobuf
at 3.20.3 only under the `all` and `functions` extras, and this project installs neither.
That is resolver evidence. The runtime evidence is the deployed check in `tasks.md`, and it
must pass before merge.

This change also corrects section 2.2 of `docs/docs/proposals/opentelemetry-readiness.md`, so
the merged document does not keep recommending an option that cannot be installed.

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
