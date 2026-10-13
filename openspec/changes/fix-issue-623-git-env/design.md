## Context

`archi create` records git provenance with four functions. Each runs git with `cwd` set to
a checkout and inherits the full process environment:

- `get_git_information(wd)` — `git branch` (`subprocess.call`) and `git rev-parse HEAD`
  (`templates_manager.py:147-172`), then `capture_git_diff(wd)`.
- `capture_git_diff(wd)` — `git diff` and `git diff --stat`
  (`git_diff_capture.py:75-107`).
- `resolve_source_commit(repo_root)` — `git rev-parse --short HEAD` and
  `git status --porcelain` (`source_version.py:49-78`).
- `get_git_version()` — `git describe --tags --always --dirty`
  (`templates_manager.py:175-190`).

git gives `GIT_DIR` and its relatives priority over `cwd` discovery. A caller that
exports them (a git hook, a worktree hook, a wrapper script) silently redirects all
four functions to another repository.

## Goals / Non-Goals

**Goals:** every git subprocess in the four functions describes the checkout at its
`cwd`. Output is byte-identical when none of the listed variables is set.

**Non-Goals:** other git calls in `src/cli` (`cli_main.py`, `deployment_manager.py`,
`base_image_preflight.py`, `utils/`) — they do not record provenance. Variables that
change git's configuration but not the repository (`GIT_CONFIG_GLOBAL`,
`GIT_CONFIG_NOSYSTEM`, `GIT_CEILING_DIRECTORIES`) stay, so tests and operators can
still set them.

## Decisions

### D1. One helper module, `src/cli/managers/git_env.py`
`git_env()` returns `{k: v for k, v in os.environ.items() if k not in REPO_SELECTION_VARS}`.
`REPO_SELECTION_VARS` is a module-level frozenset of the eight names in the proposal. The
helper reads `os.environ` at call time, not at import, so `monkeypatch.setenv` in tests
takes effect. A separate module keeps `source_version.py` from importing the diff module.

### D2. Remove, do not override
Remove the variables instead of setting `GIT_DIR` to `wd/.git`. A linked worktree has a
`.git` file, not a directory, and a nested `wd` has no `.git` at all; git's own discovery
from `cwd` already handles both.

### D3. Tests use real repositories
Each test builds repository A (the checkout) and repository B (a different HEAD) under
`tmp_path`. It deletes every inherited `GIT_*` variable before it creates them (the #622
fixture rule), sets `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`, then sets
`GIT_DIR`, `GIT_WORK_TREE`, and `GIT_INDEX_FILE` to B with `monkeypatch.setenv`. The
function under test must report A. Before the fix it reports B.

## Risks / Trade-offs

- A caller that relies on `GIT_DIR` to point provenance at a bare repository loses that.
  No such caller exists in the tree (`grep -rn GIT_DIR src` finds none).
- New tests that call `git init` must strip `GIT_*` themselves, or a hook run can rewrite
  the repository that runs the hook (the #622 incident: `core.bare = true` in the shared `.git/config`).
