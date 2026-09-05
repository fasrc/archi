# Open questions

The Ralph loop appends here when it hits a decision the specs don't cover, then
stops without committing code (see PROMPT.md "Stop conditions"). Answer a question
by resolving it in the relevant spec or a decision record, then remove it from this
list. An empty list below means nothing is currently blocked.

<!-- The loop appends entries below this line. -->

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

## Task 3.1 — "Push and open the PR"

**Status: BLOCKED — PAT lacks `Contents: write` on `fasrc/archi`.**

`git push -u origin fix/issue-426-bench-out-strict-json` returns HTTP 403
("Permission to fasrc/archi.git denied to swinney.") every invocation. The
ambient `GH_TOKEN` is a fine-grained PAT scoped to Contents: **read** only. The
git-over-HTTPS push path requires Contents: **write**. A previous loop turn
recorded the step-5 stop condition in `tasks.md` (commit `9870bf3c`) and pushed
the branch to the fork (`swinney/archi` at `e18d6614`), but `fasrc/archi` still
does not have the branch.

**Decision needed from a human operator:** either push the branch to `fasrc/archi`
directly (`git push fasrc/archi fix/issue-426-bench-out-strict-json`) and open the
PR manually using the body described in task 3.1, OR grant the PAT Contents: write
on `fasrc/archi` so the loop can complete step 6 (tick the task, record the PR URL,
commit). The work itself (JSON migration, report re-renders, tests) is fully
complete on local `HEAD` (`9870bf3c`) and on the fork.
