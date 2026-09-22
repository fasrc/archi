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

## Task 5.2 (fix-issue-520) — push to fasrc/archi blocked by PAT scope

**Status: BLOCKED — fine-grained PAT cannot push git objects to `fasrc/archi`.**

The branch `fix/issue-520-read-four-requirement-spellings` is pushed to the fork at
`https://github.com/swinney/archi/tree/fix/issue-520-read-four-requirement-spellings`.
Gate is green (4744 passed, diff is tests-only so diff-cover prints "No lines with
coverage information", 2026-09-22). All four task-specific commits are on HEAD:

```
40f16f9c chore(#520): mark task 5.1 complete (acceptance oracle passed)
b19071fc fix(#520): exempt a detached extras list in front of a comparison
c0712f32 fix(#520): cut pip's short config-settings option from a requirement line
8a86941e fix(#520): read a continued environment marker in the conditional-pin guard
44b40694 fix(#520): stop a comment line continuing onto the requirement after it
```

`git push -u origin fix/issue-520-read-four-requirement-spellings` and
`POST /repos/fasrc/archi/git/blobs` both fail with HTTP 403:
"Resource not accessible by personal access token". The PAT has `contents: read` on
`fasrc/archi` but not `contents: write`.

The PR body is ready at `/tmp/pr-body-520.md` (not committed; re-run the loop to
regenerate it, or see `openspec/changes/fix-issue-520-read-four-requirement-spellings/proposal.md`).

**Action needed from a human operator:** push the branch and open the PR using a PAT
that has `contents: write` on `fasrc/archi`, or use the GitHub web UI:

```bash
git push -u origin fix/issue-520-read-four-requirement-spellings
gh pr create \
  --repo fasrc/archi \
  --base dev \
  --title "fix(#520): read four more requirement spellings as pip does in the base-image dependency guard" \
  --body-file /tmp/pr-body-520.md
```

After the PR is open, task 5.3 requires replying to four review threads on PR #506
(IDs 4058046837, 4058046838, 4058046843, 4058046844) each with the SHA of the commit
that closes it (tasks 1→44b40694, 4→b19071fc, 3→8a86941e, 2→c0712f32 respectively).
That step requires the same write-access PAT.
