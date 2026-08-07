# archi — project instructions

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
