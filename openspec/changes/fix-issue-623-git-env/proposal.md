## Why

Issue #623. The deploy-time git provenance runs git with the caller's full environment.
When `GIT_DIR` (or `GIT_WORK_TREE`, `GIT_INDEX_FILE`, and related variables) is set — for
example inside a git hook, where a linked worktree exports an absolute `GIT_DIR` — git
ignores `cwd` and reads the repository that the variable names. The provenance then
records the wrong `last_commit`, diff, stat, or source commit, or fails.

Measured on 2026-10-06 (PR #622): the `repo` test fixture in
`tests/unit/test_benchmark_git_diff_bound.py` had the same leak. Under a worktree
pre-commit hook it moved the worktree HEAD and set `core.bare = true` in the shared
`.git/config`. The fixture was fixed; the production code was not.

## What Changes

- Add `git_env()` in a new module `src/cli/managers/git_env.py`. It returns a copy of
  `os.environ` without the repository-selection variables `GIT_DIR`, `GIT_WORK_TREE`,
  `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`,
  `GIT_COMMON_DIR`, `GIT_NAMESPACE`, and `GIT_PREFIX`. All other variables stay.
- Pass `env=git_env()` to every git subprocess in:
  - `get_git_information` (`src/cli/managers/templates_manager.py`),
  - `capture_git_diff` (`src/cli/managers/git_diff_capture.py`),
  - `resolve_source_commit` (`src/cli/managers/source_version.py`),
  - `get_git_version` (`src/cli/managers/templates_manager.py`).

The last two are not named in the issue body. They have the same leak and also feed
deploy provenance (`SOURCE_COMMIT` and `app_version`), which is the issue's title scope.

## Capabilities

### New Capabilities
- `deploy-git-provenance`: git provenance that `archi create` records describes the
  checkout at the given path, whatever repository-selection variables the caller exports.

### Modified Capabilities
- None.

## Impact

- Code: `src/cli/managers/git_env.py` (new), `git_diff_capture.py`, `source_version.py`,
  `templates_manager.py`.
- Tests: new `tests/unit/test_git_env.py`; new cases for the four functions.
- No config, deploy, or docs change. Output is unchanged when no such variable is set.
