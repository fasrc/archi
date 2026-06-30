## Context

`archi create` renders a deployment under `~/.archi/archi-<name>/` and the container is built
via `pip install .` from whatever the operator's working tree currently holds. There is no
record of which archi source commit produced a given deployment. Worktree/background workflows
can leave the main checkout on a stale branch, so a redeploy can silently ship the wrong code.

The artifact-preparation entrypoint is `TemplatesManager.prepare_artifacts` in
`src/cli/managers/templates_manager.py`. It builds a `TemplateContext` (which exposes
`base_dir`, the rendered artifacts directory), logs "Preparing deployment artifacts …", runs
render stages, and logs "Finished preparing …". `templates_manager.py` is a large file that is
not black-clean, so editing it heavily reflows untested lines and fails the diff-coverage gate.

## Goals / Non-Goals

**Goals:**
- Record the archi source commit (short SHA + dirty flag) at `archi create` time, to both the
  deploy log and a `SOURCE_COMMIT` file in the artifacts directory.
- Resolution is best-effort and never fatal — a deploy from a non-git directory still succeeds.
- Keep the new logic in a black-clean, fully-unit-tested module so the diff-coverage gate passes.

**Non-Goals:**
- Fixing the gitignored `deploy/fasrc-dev/**` wrapper scripts (out of repo, cannot ship via PR).
- Recording anything beyond the source commit (no build timestamps, no dependency manifests).
- Enforcing a clean tree or blocking deploys on dirty/unknown state — recording only.

## Decisions

- **New standalone module `src/cli/managers/source_version.py` with `resolve_source_commit(repo_root)`.**
  Rationale: the issue and the repo's "diff-cover black churn" history show that inlining logic
  into the large `templates_manager.py` reflows hundreds of untested lines under black and tanks
  diff coverage. A small new module is fully covered by its own unit test. *Alternative:* inline
  in `prepare_artifacts` — rejected for the gate reason.
- **Implementation via `subprocess` git calls with `cwd=repo_root`.** Run `git rev-parse --short
  HEAD` for the SHA and `git status --porcelain` for dirtiness. Wrap everything in a broad
  `except Exception` returning `unknown`. *Alternative:* a git library dependency — rejected to
  honor "avoid third-party dependencies" and the never-fatal requirement (a library import error
  would itself need guarding).
- **`repo_root` defaults to the archi package root** (derived from `__file__`) so the call site
  resolves the commit of the installed/working-tree code, matching what `pip install .` ships.
- **Write `SOURCE_COMMIT` and log next to the existing "Preparing deployment artifacts" line.**
  Both wrapped so a write/IO error is logged but never propagates.

## Risks / Trade-offs

- [Helper raises and aborts a deploy] → Broad `try/except` returning `unknown`; the call site
  also guards the file write. Unit tests cover the non-git/`unknown` path explicitly.
- [Dirty detection false-negative under a non-editable install with no `.git`] → Acceptable: that
  case correctly resolves to `unknown` (there is no source tree to inspect), which is the
  documented, honest result.
- [Short SHA ambiguity across very large histories] → Out of scope; `--short` matches what the
  acceptance criteria and humans use for greppability.

## Migration Plan

Purely additive. No data model or config schema change; no rollback needed. Existing deployments
simply gain a `SOURCE_COMMIT` file and one extra log line on the next `archi create`.

## Open Questions

None — the issue body fully specifies the behavior, paths, and acceptance criteria.
