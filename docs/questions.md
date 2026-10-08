# Open questions

The Ralph loop appends here when it hits a decision the specs don't cover, then
stops without committing code (see PROMPT.md "Stop conditions"). Answer a question
by resolving it in the relevant spec or a decision record, then remove it from this
list. An empty list below means nothing is currently blocked.

<!-- The loop appends entries below this line. -->

## Task 5.1 (fix-issue-463) — PR creation blocked by PAT scope

**Status: BLOCKED — fine-grained PAT cannot create PRs in `fasrc/archi`.**

The branch `fix/issue-463-local-mode-canonicalization` has been pushed to the fork at
`https://github.com/swinney/archi/tree/fix/issue-463-local-mode-canonicalization`.
The gate is green (4392 passed, 100% diff coverage on 39 lines, re-confirmed 2026-09-12).

`gh pr create --repo fasrc/archi --base dev` fails with HTTP 403:
"Resource not accessible by personal access token". The active PAT
(`github_pat_11AADEDXI0Sxfs6F2YCK5t_...`) is a fine-grained token scoped to
`swinney/archi` only — it cannot write to `fasrc/archi`. Cross-fork `gh pr create
--head swinney:fix/...` also fails with the same 403.

**Action needed from a human operator:** run these two commands with a PAT that has
`repo` write access to `fasrc/archi`, or open the PR via the GitHub web UI:

```
git push -u origin fix/issue-463-local-mode-canonicalization
gh pr create \
  --repo fasrc/archi \
  --base dev \
  --title "fix(#463): canonicalize and validate the local provider's mode at the config seams" \
  --body-file docs/pr-body-463.md
```

The complete PR body is in `docs/pr-body-463.md` (committed on this branch).

## Task 5.2 — "Run before/after benchmark; record recall/precision deltas"

**Status: BLOCKED — requires live infrastructure not available to the loop.**

The first unchecked task in `tasks.md` is section 5.2, which asks to *run* the
benchmark before and after the title-aware retrieval change and record the
recall/precision deltas. This cannot be executed in the Ralph loop sandbox:

- `src/bin/service_benchmark.py` reads `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and
  `HUGGING_FACE_HUB_TOKEN` via `read_secret(...)` at import time, and constructs a
  Postgres connection through `PostgresServiceFactory.from_env(...)`. None of these
  secrets are present (`.env` is empty; no `.secrets.env`).
- A real before/after run needs an **ingested corpus** whose documents have the
  title-only / filename-only keywords described in
  `src/bin/benchmark_query_sets/title_aware_query_set.json`. The harness blocks on
  the data-manager ingestion-status endpoint (`wait_for_ingestion_completion`)
  before scoring.
- The environment has **no container runtime** (neither `docker` nor `podman` on
  PATH) and **no reachable Postgres/pgvector** (port 5432 closed), so a deployment
  cannot be brought up to ingest the corpus or serve retrieval.
- Producing recall/precision numbers without that stack would mean fabricating
  results, which the spec ("Retrieval quality is benchmarked") and the loop's
  "report outcomes faithfully" rule forbid.

**Decision needed from a human operator:** run the benchmark on a real deployment
and record the deltas, OR clarify how the loop should satisfy 5.2 offline (e.g.
a fixture-backed, deterministic mini-corpus + an offline benchmark path that does
not require secrets or a container runtime). The two "baseline vs. new behavior"
configs should be the `title_header.enabled` / `title_weight` / `filename_boost`
knobs toggled off vs. on (see `add-title-aware-retrieval` design.md, Migration
Plan step 4). Until resolved, tasks 5.2–5.4 (which depend on the recorded
results) and the test/validation work in section 6 remain queued behind this.

## Task 2.1 (fix-issue-529-comment-closes-continuation) — CI red on PR #631 despite a green gate

**Status: BLOCKED — fixing it for real means editing shared CI workflow config, which
is outside this PR's scope and the loop's PAT cannot retrigger a run to confirm a flake.**

`/review-findings.md` reported PR #631 (`fix/issue-529-comment-closes-continuation`) as
CI-red and asked to "make the gate match CI and fix the failure." Checked both:

- The actual gate (`gate` job, `.github/workflows/ci.yml` → `scripts/gate.sh`, the
  project's documented single source of truth) is **green** — run `37727010875`
  completed `success` at `2026-10-08T04:27:27Z`.
- The red check is `unit-tests` in the separate `.github/workflows/pr-preview.yml`
  workflow, which runs `python -m pytest tests/unit/ -v --tb=short` directly —
  a job that predates the gate.sh consolidation (#69/#34) and duplicates, outside
  the single source, exactly the full-suite run `gate.sh` already does.
- The failure is
  `tests/unit/evaluation/qa/test_jobs_history.py::test_console_persists_and_passes_phase_workers`
  — `TimeoutError` from `service.job_manager.wait(job["id"], timeout=2)`. This PR's
  diff touches only `openspec/` docs and
  `tests/unit/test_base_image_dependency_compatibility.py`; zero lines in
  `src/evaluation/` or `tests/unit/evaluation/`. Re-running that one test locally in
  isolation passes (6.65s). It is a real-thread/subprocess test with a hardcoded 2s
  timeout; issue #435 already tracks a sibling flake in the same job-manager test
  family (`test_job_manager_terminates_running_evaluation_process`, "flaky ~3 in 5,
  blocks the commit gate").
- `gh run rerun 37727010872 --failed` returned `403 Resource not accessible by
  personal access token` — the loop's fine-grained PAT cannot retrigger Actions runs
  (same class of PAT-scope block as the task 5.1 entry above).

**Decision needed from a human operator:** either (a) grant the loop's PAT
`actions:write` so it can retrigger a flaky `pr-preview.yml` run itself, (b) decide
whether `pr-preview.yml`'s `unit-tests` (and `lint`) jobs should be retired or folded
into the single-source `gate` job per the consolidation `scripts/gate.sh`'s header
describes, updating branch protection's required checks to match, or (c) manually
re-run the failed job from the GitHub web UI. Fixing the flaky test itself is out of
scope for #529 and would violate "never modify another subsystem's tests to make your
code pass." PR #631 is already open with the substantive checks (format, coverage,
full unit suite via `gate.sh`) green; only this duplicate check blocks it.
