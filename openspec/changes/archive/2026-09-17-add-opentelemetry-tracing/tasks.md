# Tasks — default-off OpenTelemetry tracing

Builds phases 1 and 2 of `docs/docs/proposals/opentelemetry-readiness.md` (PR #446).

**Read this first.** Every checkbox must end with `bash scripts/gate.sh` green and must be
committed on its own. TDD happens *inside* a checkbox: write the failing test, run it, watch
it fail, implement, watch it pass, commit. Do not split a red test and its fix across two
checkboxes. A checkbox that ends red can never be committed.

`src/interfaces/chat_app/app.py` is over 5000 lines and lightly covered. Keep every call site
to one line and put the logic in `src/utils/telemetry.py`.

## 1. Dependencies

- [x] 1.1 `model: sonnet` — **Pin the suite in four files, and move protobuf.**
  Add the OpenTelemetry core packages at 1.44.0, the instrumentation packages at 0.65b0, and
  `openinference-instrumentation-langchain==0.1.74` to `pyproject.toml` and
  `requirements/requirements-base.txt`. Move `protobuf` from 4.25.8 to 7.36.1 in the second
  file. Regenerate the two base-image files with the same command
  `scripts/dev/build_docker_images.sh:80-86` runs.
  `tests/unit/test_requirements_generated_in_sync.py` proves the regeneration.
  Install the same pins into the local conda `archi` environment so the gate can run the
  tests that follow.
  Correct section 2.2 of `docs/docs/proposals/opentelemetry-readiness.md`: it recommends
  option A, and option A cannot be installed. Record the measurement, not the conclusion
  alone.

## 2. The bootstrap module

- [x] 2.1 `model: opus` — **The off path and the enable rule.**
  RED first in `tests/unit/test_telemetry.py`: with no environment variable set,
  `init_telemetry()` returns a disabled status and installs nothing. Then the endpoint alone
  enables it, the archi flag alone enables it, and the flag set to a false value beats a
  present endpoint. Use `monkeypatch.setenv`, the pattern at
  `tests/unit/test_fasrc_docs_agent.py:109`.

- [x] 2.2 `model: opus` — **The service name, and the entrypoint fallback.**
  RED first: `OTEL_SERVICE_NAME` wins; the argument comes next; the derived name comes last.
  `src/bin/service_chat.py` derives `archi-chat`. Assert the `service.name` resource
  attribute for at least two entrypoints.

- [x] 2.3 `model: opus` — **Fail open at init.**
  RED first: force an instrumentor to raise, then assert that `init_telemetry()` returns a
  disabled status, logs one warning, and raises nothing.

- [x] 2.4 `model: opus` — **Fail open on export.**
  RED first: point the exporter at an unreachable endpoint, force a flush, and assert one
  warning in the log and a live process. An export failure surfaces on the batch processor's
  thread, not at init, so task 2.3 cannot stand in for this one.

- [x] 2.5 `model: opus` — **Drop the query string from URL attributes.**
  RED first: a span with a URL attribute that holds `?code=secret` exports without the query
  string. Cover `http.url`, `url.full` and `http.target`.

- [x] 2.6 `model: opus` — **Hide model content by default.**
  RED first: with default flags the OpenInference `TraceConfig` hides inputs and outputs;
  with `ARCHI_OTEL_CAPTURE_CONTENT=true` it does not.

## 3. The seams

- [x] 3.1 `model: opus` — **Trace IDs in the log, in the right order.**
  RED first: telemetry off renders the plain format; a failed logging instrumentor still
  renders the plain format; a line logged inside an active span carries that span's trace ID.
  Then change `setup_logging()` to select the format after the instrumentor succeeds.

- [x] 3.2 `model: sonnet` — **One Flask seam per app.**
  RED first: `instrument_flask_app()` is a no-op when telemetry is off, and it excludes the
  SSO redirect route when telemetry is on. Then add one call line to
  `src/bin/service_chat.py`, `src/bin/service_grader.py` and
  `src/bin/service_data_manager.py`.

- [x] 3.3 `model: sonnet` — **A service name per compose block.**
  RED first: extend the compose rendering test so every service block that runs an archi
  process renders a distinct `OTEL_SERVICE_NAME`. Then edit
  `src/cli/templates/base-compose.yaml`.

- [x] 3.4 `model: opus` — **The worker thread already carries the context.**
  RED first, and this one is a pin, not a fix: log a line from a `ThreadPoolExecutor` worker
  entered through `contextvars.copy_context().run`, inside an active span, and assert the
  trace ID reaches it. `src/interfaces/chat_app/app.py:2245,2264` is the shape this test
  pins. No instrumentor and no production edit belong in this task.

## 4. Documentation

- [x] 4.1 `model: sonnet` — **Write the operator page.**
  Add `docs/docs/observability.md`: the two environment variables that turn tracing on, the
  content flag and its privacy meaning, the service names, and the statement that archi ships
  no receiver. Add it to `docs/mkdocs.yml` if the nav lists pages by hand.
  `mkdocs build --strict` must pass.

## 5. Before merge

- [x] 5.1 `model: opus` — **Adversarial review loop.**
  Run `/codex:adversarial-review --wait` on the branch. Verify each finding against the code.
  Fix what holds, push back on what does not, then run it again. Stop at a clean round or at
  nits, and file the nits as issues.

- [x] 5.2 `model: opus` — **Deployed validation.** `AGENTS.md:61-63` requires it.
  Run one streamed chat request against the preview deploy or the dev stack with
  `OTEL_EXPORTER_OTLP_ENDPOINT` set at a receiver. Confirm two things: the receiver holds a
  span for that request, and the service log holds a line with the same trace ID.

### What 5.2 ran, and what it found

Ran on 2026-09-10 against a container deployment built from this branch
(`SOURCE_COMMIT bd2ee627`), with Arize Phoenix 20.9.0 as the receiver at
`http://192.168.1.128:6006`. Three containers: `chatbot-otelcheck`,
`data-manager-otelcheck`, `postgres-otelcheck`. One streamed chat request to
`/api/get_chat_response_stream`, answered by a real model server.

Both required results hold:

- **The receiver holds the trace.** 15 spans in one trace, rooted at
  `POST /api/get_chat_response_stream` with no parent. Under it: `LangGraph`,
  `model`, `ChatOpenAI`, `search_vectorstore_hybrid`, `HybridRetriever`, the outbound
  `POST` to the model server, and the Postgres statements. The agent work runs in a
  worker thread, and it is parented correctly, so the copied context reaches the
  thread in a deployment and not only in a test.
- **The log carries the same trace.** 37 lines in the chat container's log render
  `trace_id=f9d0c0db783675f792e1cc53d92cf101`, matching the span, and the span id
  alongside it.

Also confirmed on the same run: the resource carries `service.name = archi-chat`;
`input.value` and `output.value` are `__REDACTED__` on the LangGraph, ChatOpenAI and
HybridRetriever spans; and the real OTLP HTTP exporter under `protobuf==7.36.1`
serialises to a receiver that decodes it.

**It found a defect no unit test had.** The first run exported the user's question and
archi's whole reply to the receiver, inside `db.statement` on a Postgres span, with
content capture off. Nearly every archi statement is parameterised, so its text is
only shape; `SQL_INSERT_CONVO` goes through `psycopg2.extras.execute_values`, which
expands the rows into the statement before sending it. The chunk insert leaked
document text the same way, and carried an 8388 character embedding array besides.
Fixed in `7d25a470` and re-checked on a rebuilt deployment: same request, same span
tree, no conversation anywhere in the export.

**What 5.2 did not cover.** The validated request went to
`/api/get_chat_response_stream`, which wraps its generator in `stream_with_context`
(`app.py:4981`). The `/v1/chat/completions` streaming path did not, and review found
it — see task 5.3. One endpoint being correct is what hid the other.

- [x] 5.3 `model: opus` — **Preserve the request span across a streamed `/v1` response.**

      Review found `openai_compat._streaming_response()` returning
      `Response(generate())` with no `stream_with_context`. Flask tears the request
      down when the view returns, and the WSGI server pulls the generator after that,
      so the instrumentor's active span context is gone by the time
      `_chat_wrapper.stream()` runs.

      **Measured**, with the Flask instrumentor and an in-memory exporter, on two
      routes that differ only in the wrapper:

      | Route | Spans | Distinct traces | `work` span |
      |---|---|---|---|
      | `stream_with_context(body())` | 2 | 1 | child of `GET /stream` |
      | `Response(body())` | 2 | **2** | **ROOT, own trace** |

      So the failure is not a missing span. The server span still exports — the WSGI
      middleware ends it when the iterable closes — and the agent's work lands in a
      *second* trace with no parent. Log lines emitted inside that work carry the
      orphan trace id, which defeats the correlation this whole change is for, on the
      one endpoint whose traces nobody had looked at.

      Fixed by wrapping the generator. Three tests in `test_telemetry.py`
      (`TestStreamingResponsesKeepTheRequestSpan`): two pin the mechanism in both
      directions, so if a future Flask or instrumentor release parents these on its
      own the wrapper can be reconsidered on evidence rather than removed on a hunch;
      the third reads the real call site and requires the wrapper there.

### Scheduling

This change carries no milestone, and `docs/docs/proposals/release-plan-2026.md`
schedules no OpenTelemetry work — its only observability row is the parked
`#258, #227` pair. Its driver is PR #446, a merged readiness assessment, not a
milestoned gating issue.

So it merges as an explicit operator decision, not as a milestone gate. Review
correctly flagged that the proposal's original wording read as though this change
implemented the two parked issues; it does not, they keep their `parked` label, and
nothing here touches the `hybrid_search` warning. See `proposal.md`, "On #258 and
#227".
