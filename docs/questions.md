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

## Task 1.4 (fix-issue-190-doc-anchor-guard) — a red-by-design TDD step conflicts with the gate's "never commit red" rule

**Status: BLOCKED — loop-process conflict, not an ambiguity in the guard's own behavior.**

`openspec/changes/fix-issue-190-doc-anchor-guard/tasks.md` section 1 is written as an
explicit TDD arc: 1.4 adds the primary content-assertion test; 1.6 says to run it and
"confirm it FAILS naming `[ovrwarn2]` — the doc says line 2138 (a comment tail) where the
table expects the warning yield … **Watch it go red before touching the doc.** This is the
red step; the drift is real, not synthetic." Only task 3.1 repairs
`docs/docs/api_reference.md` to make the suite green again — matching design.md Decision 5.

I implemented 1.4 (`test_every_anchored_line_contains_its_expected_substring` in
`tests/unit/test_doc_anchor_guard.py`) and confirmed it fails exactly as 1.6 predicts: both
the `[ovrwarn2]` link definition (doc line 183) and its inline citation (doc line 166) name
`app.py:2138`, which has no `_EXPECTED_SUBSTRINGS` entry (the table has 2143 instead, per
1.3's note) — a clean, single-cause failure with no other anchors implicated.

But `scripts/gate.sh` runs the whole `tests/unit/` suite, and `hooks/pre-commit`
unconditionally blocks any commit while the gate is red (PROMPT.md/this session's rules:
"never bypass it with `git commit --no-verify`", "do not commit red"). With 1.4's test in
place and the doc not yet repaired, `pytest tests/unit/` has one failing test, so **no commit
is possible** until 3.1's doc fix lands — regardless of how correct 1.4's own change is. I
verified this is not an environment artifact: with `bin/jq` (an untracked, already-downloaded
static binary — see `0a157cdc`/#225) added to `PATH` so the unrelated shell-suite gate steps
pass, `pytest tests/unit/` still reports exactly `1 failed, 1738 passed` — the doc-anchor test.

This session's operating rule is also "your entire job this invocation is the first
unchecked task — nothing else"; 1.4 is a distinct checklist item from 3.1–3.3, and this
branch's own history (`a5240ba5` adds 1.2's parsers; `a34b5fea` checks off 1.2 in a separate
commit with no code change) shows this task list is meant to be worked one checkbox at a
time. Every OTHER `fix-issue-*` change in this repo's history, by contrast, lands its test
and its fix in one commit (e.g. `f19c06612` for #195, `eb5ec6145` for #187) — there is no
precedent anywhere in this repo for a committed red test.

**Decision needed from a human operator:** should a red-by-design interim TDD step like 1.4
(and 1.5, 2.1–2.3) stay uncommitted in the working tree across invocations until task 3
makes the suite green — at which point one invocation commits the accumulated 1.4–3.3 diff
in one shot — or should `tasks.md` be restructured so each individually-checked-off task is
already gate-green (e.g. folding 1.4–3.3 into a single task)? Until resolved, I've made the
1.4 code change locally but left `tasks.md` 1.4 **unchecked** and made **no commit** this
turn, per rule 7 ("mark complete … if and only if … done and green") and the "do not commit
red" rule.

Separately, and not blocking on its own: this sandbox has no system `jq`, which
`scripts/ci/test_pr_readiness_labels.sh` requires. An already-downloaded static binary sits
at `bin/jq` (git-excluded via `.git/info/exclude` — the exact improvisation residue
`0a157cdc` warned about) but isn't on `PATH`. Branch `origin/fix/issue-195-timing-bool-nonfinite`
hit the same gap and fixed it by adding `export PATH="$REPO_ROOT/bin:$PATH"` to
`scripts/gate.sh`; that fix never reached `dev`. I did not apply it here since it wasn't this
turn's actual blocker, but any future invocation on any branch in this environment will hit
it too.
