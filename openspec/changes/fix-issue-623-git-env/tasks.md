# Tasks — fix-issue-623-git-env

TDD in every task: write the failing test first, run it and watch it fail, then write the
minimum code. Each task ends green and ends with one commit through the gate
(`bash scripts/gate.sh` via the pre-commit hook; never `--no-verify`). Put new tests in
new files or above an existing test, never after a file's last line. Every test that
runs `git init`/`add`/`commit` deletes all inherited `GIT_*` variables first (design D3).

## 1. The helper

- [x] 1.1 Red then green: `tests/unit/test_git_env.py` — `git_env()` omits each of the eight names in `REPO_SELECTION_VARS` (set them all with `monkeypatch.setenv`), keeps `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_NOSYSTEM`, and `PATH` with their values, reads `os.environ` at call time (set a variable after import), and returns a new dict (mutating it does not change `os.environ`). Then add `src/cli/managers/git_env.py` (design D1). Commit.

## 2. The four call sites

- [x] 2.1 Red then green: new `tests/unit/test_git_provenance_env.py` with a fixture that builds repository A (one commit, then an uncommitted change to a tracked file) and repository B (a different commit), then sets `GIT_DIR`=B/.git, `GIT_WORK_TREE`=B, `GIT_INDEX_FILE`=B/.git/index. Assert `get_git_information(wd=A)["last_commit"].strip()` equals A's HEAD and `git_diff` contains A's change and not B's; assert `capture_git_diff(A)` likewise. Watch both fail. Then pass `env=git_env()` to both git calls in `get_git_information` and both in `capture_git_diff`. Commit.
- [ ] 2.2 Red then green: in the same test file, assert `resolve_source_commit(A)` equals A's `git rev-parse --short HEAD` plus `-dirty` (A has an uncommitted change), and equals the bare short HEAD after A's change is committed. For `get_git_version()` (its `cwd` is fixed to the package directory), replace `templates_manager.subprocess.check_output` with a spy via `monkeypatch.setattr` and assert the `env` keyword it receives exists and has no `GIT_DIR`. Watch them fail. Then pass `env=git_env()` to both calls in `resolve_source_commit` and to the call in `get_git_version`. Before the commit, check that `grep -n 'subprocess\.' src/cli/managers/templates_manager.py src/cli/managers/git_diff_capture.py src/cli/managers/source_version.py` shows `env=` on every git call inside the four functions, and that `python -m pytest -q tests/unit/test_benchmark_git_diff_bound.py tests/unit/test_source_version.py tests/unit/test_benchmark_host_provenance.py tests/unit/test_templates_source_commit.py` passes. Commit.

## 3. Publish

- [ ] 3.1 Push the branch (`git push -u origin fix/issue-623-git-env`) and open the PR: `gh pr create --repo fasrc/archi --base dev --head fix/issue-623-git-env`, title `fix(cli): ignore inherited GIT_DIR in deploy git provenance`, body with `Closes #623` on its own line, a summary of the four call sites, and a note that `resolve_source_commit` and `get_git_version` are beyond the issue's named functions but inside its title scope.
