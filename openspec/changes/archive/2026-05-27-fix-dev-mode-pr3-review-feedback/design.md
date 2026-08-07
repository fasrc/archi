## Context

PR #3 introduced `archi create --dev`, which bind-mounts the local checkout's `src/` and `config/agents/` into the running containers so Python edits take effect on `docker restart` instead of requiring a full image rebuild. Three issues surfaced in code review:

1. `service_builder.py` imports `from src.cli.utils._repository_info import REPO_PATH`, but no `_repository_info.py` exists in the branch. `archi create --dev` therefore crashes with `ModuleNotFoundError` before any compose rendering happens.
2. `PYTHONDONTWRITEBYTECODE: 1` is set on the `chatbot` service only. Seven services bind-mount `src/`; the other six will write `.pyc` files into the host repo.
3. There are zero tests for the new code path. The CI workflow `pr-preview.yml` has a `unit-tests` job but nothing under `tests/unit/` exercises the CLI, the template renderer, or `DeploymentPlan` plumbing — which is why this regression slipped through.

The current branch state is otherwise healthy: the macros are clean, the docs match the behavior, and the `dev_mode=False` baseline renders identically to the pre-PR template.

## Goals / Non-Goals

**Goals:**
- Make `archi create --dev` work on the first call from any checkout that has a `pyproject.toml` at its root.
- Apply `PYTHONDONTWRITEBYTECODE` uniformly to every service that gets the dev `src/` mount, via the existing Jinja macros so the two cannot drift.
- Add the three minimum tests we identified pre-compaction so `pr-preview.yml` actually exercises this code on every future PR.
- Keep the non-dev compose output byte-identical (or at least YAML-structurally identical) to today.

**Non-Goals:**
- Reworking the dev-mode UX (the warning, the flag name, the docs are all fine).
- Supporting installs that lack a checkout (e.g., wheel-only installs) — those should fail fast with an actionable message, not silently degrade.
- Hot reload, file-watching, or volume hashing — restart-only is the explicit contract.
- Bumping the workflow file. The existing `unit-tests` job already runs `pytest tests/unit/`; new tests under that path are picked up automatically.

## Decisions

### Decision 1: Inline repo-path discovery, no new module

Replace the broken `from src.cli.utils._repository_info import REPO_PATH` with a small helper inside `service_builder.py` (or a sibling `repo_path.py` if it grows). The helper walks parents of `__file__` until it finds a `pyproject.toml`; if it hits the filesystem root first, it raises `click.ClickException` with a message that names `--dev` and tells the operator to invoke from a git checkout.

**Why not** add `_repository_info.py` as originally implied: the PR review showed the import target was a phantom — there's no other code that would benefit from it. Inlining keeps the surface area small and makes the failure mode obvious.

**Why not** `git rev-parse --show-toplevel`: introduces a shell-out and fails when the checkout is a tarball or `pip install -e .` from a non-git source. `pyproject.toml` is always present in any plausible install layout.

**Why not** an env var (`ARCHI_REPO_PATH`): adds a footgun. Operators forgetting to set it would get cryptic compose errors.

### Decision 2: Push `PYTHONDONTWRITEBYTECODE` into the dev-mount macros

Today the env var is hard-coded in the `chatbot` block of `base-compose.yaml`. Move it into `dev_src_mount()` and `dev_agents_mount()` as an `environment:` emission, OR add a sibling macro `dev_env()` that callers place inside their `environment:` block.

Preferred shape: a `dev_env()` macro callers invoke once inside the service's `environment:` block, mirroring the pattern of `dev_src_mount()`/`dev_agents_mount()`. This keeps the macro single-purpose and lets services that don't need byte-code suppression (e.g., postgres, config-seed) opt out by simply not calling it. Every service that already calls `dev_agents_mount()` or `dev_src_mount()` gets an accompanying `dev_env()` call.

**Why not** force it via a top-level `x-archi-dev: &dev_env` YAML anchor: anchors don't compose cleanly with our Jinja template structure, and the macro pattern is already established in the same file.

### Decision 3: Three unit tests, picked up by the existing CI job

Add under `tests/unit/`:

- **`test_dev_mode_compose_render.py`** — renders `base-compose.yaml` with `dev_mode=True` and `dev_mode=False`, asserts on parsed-YAML structure (PyYAML), not raw bytes, so cosmetic whitespace doesn't cause flakes. Covers Requirements: dev mounts present, `PYTHONDONTWRITEBYTECODE` everywhere it should be, baseline preserved.
- **`test_deployment_plan_dev_mode.py`** — instantiates `ServiceBuilder.build_compose_config` with and without `dev=True`, asserts `to_template_vars()` exposes the right keys and types. Covers Requirement: DeploymentPlan plumbing.
- **`test_cli_create_dev_smoke.py`** — uses `click.testing.CliRunner` to run `archi create --dev --dry-run -n test -c <fixture> -e <fixture> --services chatbot --hostmode`, asserts exit code 0 and that the dev-mode warning text is in `result.output`. Covers Requirements: warning emitted, dry-run path doesn't crash.

Tests run via the existing `python -m pytest tests/unit/ -v --tb=short` invocation in `.github/workflows/pr-preview.yml`. No workflow edits needed.

**Why not** an integration test that actually deploys: too slow, requires Docker in CI, doesn't add evidence the unit tests miss.

**Why not** a Bash-level golden-file diff: PyYAML normalization handles incidental whitespace; a byte-for-byte golden would fight the Jinja macro indentation and produce noisy diffs.

### Decision 4: Type `repo_path` as `Path`

`DeploymentPlan.base_dir` is already `Path`. `repo_path` should match. Empty default becomes `Path("")` and template rendering coerces to string via `str(repo_path)` or Jinja's implicit `__str__`. This is a small ergonomic fix, not load-bearing.

## Risks / Trade-offs

- **[Risk]** Repo-path discovery returns a path the container can't actually see (e.g., user runs `archi` from a path their Docker daemon can't bind-mount on macOS/Lima or rootless Podman).
  → Mitigation: out of scope for this change. Already true of the current PR. Document in `developer_guide.md` if it bites someone; for now, the dev warning is enough.

- **[Risk]** `PYTHONDONTWRITEBYTECODE` in services that don't actually run Python (e.g., grafana, postgres) is wasted noise.
  → Mitigation: services that don't get the dev `src/` mount also don't call the dev macros, so they don't get the env var. Pure additive.

- **[Risk]** The unit tests pin behavior tightly enough that future template edits cause noisy churn.
  → Mitigation: assert on the *parsed* structure, not on whole-file string equality. Add a single dev-vs-baseline `assert set(services) == set(...)` check rather than diffing entire YAML.

- **[Trade-off]** Walking parents for `pyproject.toml` is slightly slower than a constant. Negligible; runs once per `archi create`.

## Migration Plan

This change applies on top of the existing `feat/dev-mode-mounts` branch. Strategy options for landing:

1. **Push fixes directly onto `feat/dev-mode-mounts`** (the PR #3 branch). Reviewers see the diff inline on PR #3.
2. **Open a stacked PR against `feat/dev-mode-mounts`** so review of the fixes is independent of the original PR.

Preferred: option 1. PR #3 has not been merged; the fixes are review-feedback responses, which is the conventional pattern for "address comments". No migration of users since `--dev` has never worked end-to-end.

Rollback: revert the fix commits on the branch. The base `dev` branch is untouched.

## Open Questions

- Should `dev_env()` also set `PYTHONUNBUFFERED=1` so container logs are line-buffered for `docker logs -f`? Probably yes, but defer — out of scope of the review feedback.
- Should the dev-mode warning include the resolved `repo_path` so operators can verify what's being mounted? Likely yes; cheap, no downside.
