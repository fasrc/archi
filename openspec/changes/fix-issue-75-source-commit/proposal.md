## Why

Today nothing records which archi **source** commit produced a deployment. Background and
worktree workflows can leave the main checkout on a stale or wrong branch; because the deploy
is built via `pip install .` from whatever the operator's working tree holds, a redeploy at
that moment silently ships stale code with zero indication in the logs or artifacts. Recording
the source SHA at `archi create` time makes the shipped code auditable and greppable after the
fact.

## What Changes

- Add a small, best-effort helper that resolves the archi source git commit (short SHA plus a
  `-dirty` suffix when the working tree has uncommitted changes), returning `unknown` on any
  failure (non-git checkout, git missing). The helper **never raises** — a deploy must never
  fail because git metadata is unavailable.
- In `prepare_artifacts` (`src/cli/managers/templates_manager.py`), record the resolved value:
  - `logger.info` it next to the existing "Preparing deployment artifacts" log line, and
  - write it to a `SOURCE_COMMIT` file in the rendered artifacts directory (`context.base_dir`,
    e.g. `~/.archi/archi-<name>/`) alongside the rendered config.
- Add unit tests covering the clean, dirty, and `unknown` (non-git) resolution cases without
  depending on the test runner's own git state.

## Capabilities

### New Capabilities
- `deployment-source-commit`: At deployment-artifact preparation time, `archi create` resolves
  and records the archi source git commit it built from — emitting it to the deploy log and
  writing a `SOURCE_COMMIT` file into the artifacts directory — best-effort and never fatal.

### Modified Capabilities
<!-- None: this is purely additive provenance recording; no existing spec requirements change. -->

## Impact

- **New file:** `src/cli/managers/source_version.py` (kept black-clean and standalone so the
  diff-coverage gate sees a fully-tested unit, rather than reflowing the large
  `templates_manager.py` body).
- **Modified:** `src/cli/managers/templates_manager.py` — `prepare_artifacts` gains a log line
  and a `SOURCE_COMMIT` file write.
- **New tests:** `tests/unit/test_source_version.py`.
- **No** changes under the gitignored `deploy/fasrc-dev/**`. No new runtime dependencies.
