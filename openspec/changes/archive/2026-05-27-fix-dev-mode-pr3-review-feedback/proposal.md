## Why

PR #3 (`feat/dev-mode-mounts`) ships the `--dev` flag for `archi create` but is unshippable as written: `src/cli/utils/service_builder.py` imports `from src.cli.utils._repository_info import REPO_PATH` and that module does not exist in the branch. Running `archi create --dev` raises `ModuleNotFoundError` before any compose rendering happens. In addition, the PR's `PYTHONDONTWRITEBYTECODE: 1` guard is set on the `chatbot` service only, even though seven services bind-mount the repo's `src/` in dev mode — so the other six will write `.pyc` files into the host repo. And the dev-mode template branches and `DeploymentPlan` plumbing have no test coverage, so this regression slipped through CI.

## What Changes

- **Replace the missing `_repository_info` import** with an inline repo-path discovery helper (walk up from `__file__` until a `pyproject.toml` is found, or fall back to `git rev-parse --show-toplevel`). Surface a clean `ClickException` if the repo root cannot be located.
- **Apply `PYTHONDONTWRITEBYTECODE: 1`** to every service that bind-mounts `src/` in dev mode (`data-manager`, `chatbot`, `grader`, `piazza`, `mattermost`, `redmine`, `mailbox`, `benchmark`), not just `chatbot`. Done via the existing Jinja macros so it stays DRY.
- **Add unit tests** under `tests/unit/`:
  - `test_dev_mode_compose_render.py` — golden-style assertions that the compose template emits the expected dev bind-mounts when `dev_mode=True` and is byte-identical to the baseline when `dev_mode=False`.
  - `test_deployment_plan_dev_mode.py` — `DeploymentPlan(...).to_template_vars()` returns `dev_mode=True` and a non-empty `repo_path` when `--dev` is passed through `build_compose_config`.
  - `test_cli_create_dev_smoke.py` — Click `CliRunner` invocation of `archi create --dev --dry-run ...` succeeds and prints the dev-mode warning.
- **Type `DeploymentPlan.repo_path` as `Path`**, with the empty default expressed as `Path("")`, so it stays consistent with `base_dir: Path`.

## Capabilities

### New Capabilities
- `cli-dev-mode`: The `archi create --dev` flag, repo-path discovery, byte-code suppression in dev containers, and the compose-template dev mounts. Captures the externally observable behavior of dev mode end to end.

### Modified Capabilities

None — `cli-dev-mode` is the only spec touched by this change, and it is being introduced here.

## Impact

- **Code**: `src/cli/utils/service_builder.py` (replace broken import), `src/cli/templates/base-compose.yaml` (broaden `PYTHONDONTWRITEBYTECODE` via the dev-mount macros).
- **Tests**: three new files under `tests/unit/`. Picked up automatically by the existing `.github/workflows/pr-preview.yml` `unit-tests` job.
- **APIs**: none. CLI surface (`archi create --dev`) is unchanged.
- **Dependencies**: none.
- **Compatibility**: `--dev` is opt-in and was never functional in the merged branch state, so there are no users to migrate. Non-dev compose output remains byte-identical.
- **Targets**: PR #3 on `fasrc/archi` (branch `feat/dev-mode-mounts`).
