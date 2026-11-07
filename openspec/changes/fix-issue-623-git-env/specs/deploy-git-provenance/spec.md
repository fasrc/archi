## ADDED Requirements

### Requirement: Provenance git calls ignore inherited repository-selection variables
Every git subprocess in `get_git_information`, `capture_git_diff`, `resolve_source_commit`, and `get_git_version` SHALL run with an environment that omits `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_COMMON_DIR`, `GIT_NAMESPACE`, and `GIT_PREFIX`.
All other environment variables SHALL pass through unchanged. When none of these
variables is set, the recorded provenance SHALL be the same as before this change.

#### Scenario: GIT_DIR points at another repository
- **WHEN** `GIT_DIR`, `GIT_WORK_TREE`, and `GIT_INDEX_FILE` name repository B and `get_git_information(wd=A)` runs
- **THEN** `last_commit` is A's HEAD and `git_diff` shows A's uncommitted change, not B's

#### Scenario: Source commit under a hook environment
- **WHEN** `GIT_DIR` names repository B and `resolve_source_commit(A)` runs
- **THEN** the result is A's short HEAD, with `-dirty` only if A has uncommitted changes

#### Scenario: Other variables pass through
- **WHEN** `GIT_CONFIG_GLOBAL` and `PATH` are set and `git_env()` is called
- **THEN** the returned mapping contains both with their values and contains none of the eight omitted names

### Requirement: Provenance tests isolate their repositories
A unit test that creates a git repository for these functions SHALL delete every inherited `GIT_*` variable before it runs `git init`, `git add`, or `git commit`.

#### Scenario: Gate run from a linked worktree hook
- **WHEN** `bash scripts/gate.sh` runs from a linked worktree's pre-commit hook
- **THEN** `git config core.bare` in the main checkout still prints `false` afterwards
