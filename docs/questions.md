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
The gate is green (100% diff coverage, all tests pass). The PR body is ready.

However, `gh pr create --repo fasrc/archi --base dev` fails with HTTP 403:
"Resource not accessible by personal access token". The active PAT
(`github_pat_11AADEDXI0Sxfs6F2YCK5t_...`) is a fine-grained token scoped to
`swinney/archi` only — it cannot write to `fasrc/archi`.

**Action needed from a human operator:** open the PR from the fork to `fasrc/archi:dev`
via the GitHub web UI, or re-run the loop with a PAT that has `repo` write access to
`fasrc/archi`. The full PR body is in `docs/questions.md` PR_BODY section below and
was also drafted in the loop's last invocation output.

**Required PR body content (per task 5.1 step 4):**
- `Closes #463`
- before/after probe table (in the loop output)
- the behavior change: a deployment with an unrecognized `mode` that silently ran Ollama
  now fails at construction with a named error
- deviation from `grep -c` criterion: count is 0, not 1, in `local_provider.py` — see
  design.md Decision 1
- why module went to `src/utils/`: LangChain import cost via `src/archi/providers/__init__`
  — see design.md Decision 1
- amendment to unarchived #450 change directory — see design.md Decision 7
- out-of-scope note on seam precedence divergence — see design.md Decision 3
- five restated tests named — see design.md Decision 9
- second pre-existing defect: present-but-null `local_mode` key broke `list_models` and
  `validate_connection`

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
