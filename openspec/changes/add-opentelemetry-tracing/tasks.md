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

- [ ] 4.1 `model: sonnet` — **Write the operator page.**
  Add `docs/docs/observability.md`: the two environment variables that turn tracing on, the
  content flag and its privacy meaning, the service names, and the statement that archi ships
  no receiver. Add it to `docs/mkdocs.yml` if the nav lists pages by hand.
  `mkdocs build --strict` must pass.

## 5. Before merge

- [ ] 5.1 `model: opus` — **Adversarial review loop.**
  Run `/codex:adversarial-review --wait` on the branch. Verify each finding against the code.
  Fix what holds, push back on what does not, then run it again. Stop at a clean round or at
  nits, and file the nits as issues.

- [ ] 5.2 `model: opus` — **Deployed validation.** `AGENTS.md:61-63` requires it.
  Run one streamed chat request against the preview deploy or the dev stack with
  `OTEL_EXPORTER_OTLP_ENDPOINT` set at a receiver. Confirm two things: the receiver holds a
  span for that request, and the service log holds a line with the same trace ID.
  This task needs a receiver and a running deployment. It is the one task a reviewer must
  see evidence for before merge.
