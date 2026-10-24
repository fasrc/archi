## Why

The base-image preflight refuses a source root that `copy_source_code()` cannot use, before
`archi create --force` or `archi evaluate --force` tears down the existing deployment
(fasrc/archi#394). The check tests existence only, so it says less than it claims
(fasrc/archi#445):

- `src/cli/managers/base_image_preflight.py:97` —
  `unreadable = [name for name in _COPIED_SOURCE_PATHS if not (root / name).exists()]`.
  The variable says "unreadable"; the test is `exists()`.
- `:119` — the refusal text says `"{whose} is missing {…}"` for every failure.
- `src/cli/managers/templates_manager.py:1309` `copy_source_code` copies `src` with
  `shutil.copytree` (`:1346`) and the two files with `shutil.copyfile` (`:1348`). Both raise
  on a path that exists but cannot be read, and the copy runs below the teardown.

So a root whose `src`, `pyproject.toml` or `LICENSE` exists but cannot be read passes the
preflight. `--force` then removes the working deployment, and the copy fails after it.

The operator decided on 2026-09-26 (issue comment): option B — test readability, and make
the refusal text tell "missing" apart from "unreadable". A walk of the 311 files under
`src` measured 0.6 ms, so cost is no reason to skip it.

Anchors verified on `origin/dev` 2a92d3c3 on 2026-10-03.

## What Changes

- Classify each of `src`, `pyproject.toml` and `LICENSE` as missing (`not exists()`),
  unreadable (exists, but `os.access(path, os.R_OK)` is false, and for the `src` directory
  also `os.X_OK` is false), or usable.
- For a usable `src`, walk the tree with `os.walk` and report the first file or directory
  inside it that cannot be read (directories need `R_OK` and `X_OK`, files need `R_OK`).
- The refusal text has two shapes: `"{whose} is missing {names}"` and
  `"{whose} has unreadable {names}"`. When both apply, the text carries both lines. The
  rest of the message (the teardown explanation and the remedy) does not change.
- Rename `_refuse_a_root_the_source_copy_cannot_use` to
  `_refuse_a_root_the_source_copy_cannot_read`, and update its two callers
  (`base_image_preflight.py:81`, `:84`). No test calls it by name.
- The docstring says that the check tests readability.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-create-preflight`: the source-root refusal distinguishes a missing path from an
  unreadable one, and refuses an unreadable one. The `fix-issue-394` change that added the
  root check is still unarchived, so this delta is `ADDED`.

## Impact

- Code: `src/cli/managers/base_image_preflight.py` only.
- Tests: `tests/unit/test_base_image_preflight.py`.
- Out of scope: moving the source copy above the teardown, any change to
  `templates_manager.copy_source_code`, and the template-directory `is_dir()` check.
