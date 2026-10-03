## Context

`build_source_root()` (`src/cli/managers/base_image_preflight.py:61`) calls
`_refuse_a_root_the_source_copy_cannot_use(root, *, recorded)` (`:88`) on the recorded
checkout, or on `PACKAGE_ROOT` when no checkout is recorded. The function must predict
whether `copy_source_code()` (`templates_manager.py:1309`) will succeed. That copy uses
`shutil.copytree` on `src` and `shutil.copyfile` on `pyproject.toml` and `LICENSE`, and both
fail on a path that exists but cannot be read.

## Goals / Non-Goals

**Goals:** refuse an unreadable root before the teardown; say which paths are missing and
which are unreadable; keep the existing message frame and remedy text.

**Non-Goals:** change `copy_source_code`; move the copy above the teardown; change the
template-directory `is_dir()` check (`:98`).

## Decisions

### D1. Classify with `os.access`, not with a trial open

`os.access(path, os.R_OK)` answers for the real user and is cheap. A file needs `R_OK`.
A directory needs `R_OK` and `X_OK`, because `copytree` must list it and enter it. A trial
`open()` would also work for files but not for directories, and it gives two code paths
for one question.

Root ignores file modes, so `os.access` returns true there. That is correct: the copy also
succeeds as root.

A dangling symlink inside `src` fails `os.access` (it follows the link), and `copytree`
also fails on it. So the walk reports it as unreadable, which is the correct prediction.

### D2. Walk `src` only when `src` itself is usable

If `src` is missing or unreadable, report that and do not walk it. Otherwise walk it with
`os.walk(src, onerror=...)` and stop at the first unreadable entry. Report that entry as a
path relative to the root (for example `src/cli/foo.py`). One entry is enough for the
operator to act, and stopping early keeps the cost bounded.

`os.walk` does not follow directory symlinks by default, but `copytree` does. A symlinked
directory inside `src` therefore gets only the `os.access` check on the link target, not a
walk of its contents. That is an accepted limit: the issue asks for the first unreadable
entry under a plain tree, and the current tree has no directory symlinks under `src`.

### D3. Two message lines, one frame

Keep one `BaseImagePreflightError`. The second line of the message becomes one or two lines:

```
  {whose} is missing {missing names}.
  {whose} has unreadable {unreadable names}.
```

Print only the lines that apply, missing first. The template-directory failure stays in the
"missing" list, as today. The lines after these (the teardown explanation and the remedy) do
not change.

### D4. A small pure classifier

Put the classification in a helper `_unusable_source_paths(root) -> tuple[list[str],
list[str]]` that returns `(missing, unreadable)`. The refusal function formats the message.
Tests can drive the unreadable branch two ways: with real `chmod` (skipped when
`os.geteuid() == 0`) and with `monkeypatch.setattr(os, "access", ...)` on the module's `os`
reference, which also runs as root. The monkeypatch tests keep the patch coverage honest on
a host where the `chmod` tests skip.

### D5. Rename the function

Rename `_refuse_a_root_the_source_copy_cannot_use` to
`_refuse_a_root_the_source_copy_cannot_read`. It has two callers, both in the same module
(`:81`, `:84`). No test refers to the name.

## Risks / Trade-offs

- A `chmod 0o000` test leaves a directory that `tmp_path` cleanup cannot remove. Each such
  test must restore the mode in a `finally` block.
- The walk adds about 0.6 ms on the current tree. That is acceptable for a check that runs
  once per `create` or `evaluate`.
