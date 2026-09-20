## ADDED Requirements

### Requirement: The deploy records a bounded diff in `git_info.yaml`

`archi create` SHALL cap the unified diff it records under `git_diff` at `GIT_DIFF_MAX_BYTES` (256 000 UTF-8 bytes), SHALL cut only at a line boundary and never inside a character, and SHALL record `git_diff_truncated` and `git_diff_original_bytes` beside it so a reader can tell a cut diff from a complete one.

Today `get_git_information()` (`src/cli/managers/templates_manager.py:160-163`) stores the
whole `git diff` output. The 2026-09-19 golden-set artifact carried 32.9 MB of it, 93% of the
file, none of it about the run. The cap makes an artifact's size a property of the run
rather than of whatever is uncommitted in the deploying checkout.

The key name `git_diff` is kept. Three readers in `src/utils/benchmark_provenance.py`
(`:499`, `:524`, `:609`) derive `deploy_git_dirty` from its emptiness, so the bounded field
SHALL be empty exactly when the unbounded field would have been empty. A cut SHALL never
produce an empty string from a non-empty diff, even when the first line alone exceeds the
cap.

The stat is bounded structurally rather than by truncation: `--stat=120,,200` lets git keep
at most 200 file lines of at most 120 columns and always keeps the `N files changed` summary
line, so no truncation flag is needed for it.

#### Scenario: A clean tree records empty fields

- **WHEN** `get_git_information()` runs in a repository with no uncommitted change
- **THEN** `git_diff` is `""`
- **AND** `git_diff_stat` is `""`
- **AND** `git_diff_truncated` is `False`
- **AND** `git_diff_original_bytes` is `0`

#### Scenario: A small change is kept whole

- **WHEN** one tracked file has a one-line uncommitted change
- **THEN** `git_diff` holds that line as a `+` hunk line, exactly as the unbounded capture would
- **AND** `git_diff_truncated` is `False`
- **AND** `git_diff_original_bytes` equals the UTF-8 byte length of `git_diff`
- **AND** `git_diff_stat` names the file and reads `1 file changed`

#### Scenario: An oversized change is cut and marked

- **WHEN** the working tree carries more than 1 MB of uncommitted change to tracked files
- **THEN** `git_diff` is at most 256 000 UTF-8 bytes and ends with a newline
- **AND** `git_diff_truncated` is `True`
- **AND** `git_diff_original_bytes` is greater than 1 000 000
- **AND** the JSON serialization of the whole returned mapping is under 512 000 bytes

#### Scenario: A single line longer than the cap still leaves evidence

- **WHEN** the diff's first line alone is longer than the cap, so no newline falls inside it
- **THEN** `git_diff` is non-empty, at most the cap in bytes, and decodes as valid UTF-8
- **AND** `git_diff_truncated` is `True`

#### Scenario: The stat is bounded by file count

- **WHEN** 300 tracked files carry uncommitted changes
- **THEN** `git_diff_stat` has at most 203 lines
- **AND** its last line reads `300 files changed`

### Requirement: Top-level artifact directories never enter provenance

The capture SHALL exclude the repository's top-level `bench_out/` directory from the recorded diff, the recorded stat, and therefore the dirty flag, and SHALL anchor that exclusion at the repository top so the result does not depend on the directory the capture runs from.

The capture runs with `cwd` set to `src/cli/managers/`. A bare pathspec such as
`-- . ':(exclude)bench_out'` is relative to that directory, and measured on 2026-09-20 it
restricted the diff to `src/cli/managers/` alone, so a change anywhere else in the tree
stopped marking the deploy dirty. The exclusion SHALL use the `top` pathspec magic
(`:(top,exclude)bench_out`) and SHALL carry no positive pathspec.

Only the top-level `bench_out/` is excluded. That is the shape measured on the FASRC
checkout: 48 deleted `bench_out/*.json` artifacts whose former contents made up the
32.9 MB. A `bench_out` directory nested elsewhere in the tree is not covered by this
requirement.

#### Scenario: A bench_out-only deletion records a clean tree

- **WHEN** the only uncommitted change is a deleted tracked file under top-level `bench_out/`
- **THEN** `git_diff` is `""`
- **AND** `git_diff_stat` is `""`
- **AND** `git_diff_original_bytes` is `0`

#### Scenario: A mixed change records only the code path

- **WHEN** a tracked source file is edited and a tracked file under top-level `bench_out/` is deleted
- **THEN** `git_diff` names the source file
- **AND** neither `git_diff` nor `git_diff_stat` contains `bench_out`

#### Scenario: Capture from a subdirectory covers the whole tree

- **WHEN** `get_git_information(wd=<repo>/src/pkg)` runs while `src/pkg/mod.py` and a top-level `README.md` are both edited and a tracked `bench_out/` file is deleted
- **THEN** `git_diff` names both `src/pkg/mod.py` and `README.md`
- **AND** neither `git_diff` nor `git_diff_stat` contains `bench_out`

### Requirement: The dirty flag keeps its derivation and its meaning

`deploy_git_dirty` SHALL remain derived from the emptiness of `git_diff` at the three reader sites in `src/utils/benchmark_provenance.py`, SHALL be `False` for a clean tree and for a tree whose only changes sit under top-level `bench_out/`, and SHALL be `True` for any other uncommitted change, including one the cap truncated.

`compare_runs.py` Procedure E and both report renderers
(`src/utils/generate_benchmark_report.py:303`, `:1079`) read the flag. The readers are not
edited by this change; the requirement is verified by feeding the mapping the new capture
returns into `code_version()`.

#### Scenario: A clean tree is not dirty

- **WHEN** the mapping captured from a repository with no uncommitted change is passed as `deploy_git_info` to `code_version()`
- **THEN** `deploy_git_dirty` is `False`

#### Scenario: A truncated diff is still dirty

- **WHEN** the mapping captured from a repository with more than 1 MB of uncommitted change is passed as `deploy_git_info` to `code_version()`
- **THEN** `deploy_git_dirty` is `True`

#### Scenario: A bench_out-only change is not dirty

- **WHEN** the mapping captured from a repository whose only change is under top-level `bench_out/` is passed as `deploy_git_info` to `code_version()`
- **THEN** `deploy_git_dirty` is `False`

#### Scenario: Every reader still reads a field the builder writes

- **WHEN** `grep -n 'git_diff' src/utils/benchmark_provenance.py` runs after the change
- **THEN** every match reads the key `git_diff`, which `capture_git_diff()` writes
- **AND** the existing tests in `tests/unit/test_benchmark_version_stamp.py` and `tests/unit/test_benchmark_provenance_backfill.py` pass unedited
