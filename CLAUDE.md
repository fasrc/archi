# archi — project instructions

## Release plan (pinned)
**All work is evaluated against `docs/docs/proposals/release-plan-2026.md`** (adopted
2026-08-18, PR #281). Before scheduling, triaging, or starting anything:

- **Check the issue's milestone first.** The four CalVer milestones
  (`v2026.08.0`–`v2026.11.0`) hold the only gating issues; milestone order is the
  work order (benchmark integrity precedes retrieval quality — it is its evidence rig).
- **`parked` label = deliberately unscheduled.** Do not schedule, re-triage, or
  re-prioritize parked issues. An issue leaves parked only by a human deciding it
  gates a future feature release — never by aging or nightly triage.
- **`evidence-trial` label = operator-driven trial work.** Milestone-exempt while
  the trial runs; nightly automation never schedules, triages, or drains these.
  The trial PR merges only after a human records the adopt decision on the issue.
  See the plan's "Evidence trials" section.
- **New work is judged by the plan's gate bar:** it enters a milestone only if that
  release's feature is broken/wrong/dishonest without it, evidenced by a file:line,
  measured number, or repro. Anything end-user-visible in chat outranks track
  membership and rides the earliest feasible release.
- **Release mechanics:** CalVer tag `v2026.MM.N` — all milestone items closed
  (issues **and** gating PRs merged) → `bash scripts/gate.sh` green on the current
  `dev` tip → PR `dev`→`main` → dispatch `test-and-build-tag.yml` with the tag →
  **create the GitHub Release from the tag** (the workflow does **not** create the
  Release; besides building images and tagging, it can push commits directly to the
  dispatched ref — Dockerfile base-image updates after the smoke test, and a
  `pyproject.toml`/`docs/mkdocs.yml` version bump if the release PR missed it — so
  the tag may sit ahead of the release-PR merge commit, and `main`'s branch
  protection must permit those bot pushes. **Dispatch a branch, not a commit SHA**:
  the pushes target the dispatched ref by name, so a SHA dispatch fails at those
  steps mid-release — images already published, no tag), notes from the milestone's
  closed items.
  Bump `pyproject.toml` to the PEP 440 form (e.g. `2026.8.0`) in the release PR.
  Upstream's unmerged commits: cherry-pick only, no sync release. The one other
  intake path is an **evidence trial**: a pinned-SHA, hunk-classified port of an
  upstream branch snapshot, merged only on a recorded adopt decision (plan:
  "Evidence trials").

## Development Workflow
For non-trivial changes, follow the two-loop spec-driven flow — invoke `/spec-driven-workflow`
for the full steps. This project's values:

- **Branch base / PR target:** branch from `origin/dev`;
  `gh pr create --repo fasrc/archi --base dev`. `dev` is the default branch and the trunk —
  never commit to it directly. (`origin` = `fasrc/archi`; `upstream` = `archi-physics/archi`.)
- **Gate (must pass before commit):** `bash scripts/gate.sh` — black 24.10.0 + isort 6.0.1
  (format), then `pytest tests/unit/` with `diff-cover` **patch coverage `--fail-under=80`**
  vs `origin/dev`. Wired as a pre-commit hook (`core.hooksPath=hooks`); do **not** bypass with
  `--no-verify`. The gate needs the full project toolchain and runs in the loop container /
  a full-deps env — a bare login shell (no black/pytest/runtime deps) cannot execute it.
- **Uses OpenSpec?** yes — Loop 1 via `/opsx:propose` → `/opsx:apply` → `/opsx:archive`
  (changes under `openspec/changes/`, specs under `openspec/specs/`).
- **Release steps:** dev deploy via `deploy/fasrc-dev/scripts/redeploy.sh` (= `archi create
  --force`: re-renders config → re-seeds Postgres → recreates containers; data volumes
  preserved). Verify with the `archi-dev-deploy-verify` skill (HTTP 200 + the feature toggle).
- **Commit attribution:** none — no `Co-Authored-By` / session trailers on this repo's commits.
- **Don't-touch / gotchas:**
  - **Running config lives in Postgres**, seeded from `config.yaml` at deploy. Editing
    `config.yaml` + `docker restart` is a **no-op** — re-run `redeploy.sh` to apply.
  - **App runs baked site-packages code**, not the `src/` bind mount — code changes need a
    redeploy to take effect in the container.
  - `deploy/fasrc-dev/config.yaml` and the secrets env are **git-excluded / host-specific**
    (copy from `config.example.yaml`; git-source ingest needs `GIT_USERNAME`/`GIT_TOKEN`).
  - `src/interfaces/chat_app/app.py` is **not imported by unit tests**, so new lines there
    fail diff-cover — route new logic through a small tested helper module and keep `app.py`
    to thin call sites (see `config_fingerprint.py`).
