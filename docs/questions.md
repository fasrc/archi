# Open questions

The Ralph loop appends here when it hits a decision the specs don't cover, then
stops without committing code (see PROMPT.md "Stop conditions"). Answer a question
by resolving it in the relevant spec or a decision record, then remove it from this
list. An empty list below means nothing is currently blocked.

<!-- The loop appends entries below this line. -->

## Task 2.1 — PR creation blocked by token scope

**Status: BLOCKED — GH_TOKEN lacks pull_request write scope.**

All implementation work for `fix/issue-335-pin-service-dockerfiles-to-digests` is complete
and green (gate exits 0, `git status --porcelain` clean). The branch is pushed to
`swinney/archi` (fork). However:

- `git push -u origin fix/issue-335-pin-service-dockerfiles-to-digests` fails: the
  `swinney` account has no push access to `fasrc/archi` (403).
- `gh pr create --repo fasrc/archi ...` fails: `Resource not accessible by personal access
  token (createPullRequest)` — the GH_TOKEN does not have the `repo` / PR-write scope.

**Resolution needed:** a human should open the PR from
`swinney:fix/issue-335-pin-service-dockerfiles-to-digests` → `fasrc/archi:dev`, or provide
a token with PR-write access to `fasrc/archi`. The PR body should include:
- `closes #335`
- The annotation deviation note: annotation is
  `# base-image-pin: dev-4314ac4 (managed by update_service_base_images.py)` (not
  `# base image: dev-4314ac4`), because that is what the #334 script writes.
- Parse-check note: `docker build --check` on `Dockerfile-chat` exited 0; `podman
  build --pull=never` parsed the `FROM` line correctly (failed only on local image absence).

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
