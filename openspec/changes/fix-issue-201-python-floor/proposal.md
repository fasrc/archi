## Why

`pyproject.toml:5` declares `requires-python = ">=3.7"`. That declaration is false, and has
been for some time. Re-derived on `origin/dev` at `0a157cdc`:

- `src/bin/service_benchmark.py:804` and `:864` use `match` statements — **3.10+ syntax**. On
  3.9 that is a `SyntaxError` at parse time, not a graceful degradation, so the module cannot
  even be imported on three of the five interpreters the project claims.
- `pyproject.toml:78` pins pyright to `pythonVersion = "3.11"`. Static analysis has therefore
  never once checked the declared floor.
- Every CI workflow pins `python-version: "3.11"`. Nothing has ever run 3.7, 3.8, or 3.9.

The declaration and the type-checker target contradict each other **inside the same file**,
which is what makes this mechanically detectable rather than a matter of taste.

The cost is not hypothetical. During review of PR #192 (issue #183) a reviewer raised a **P1**
against `os.path.realpath(path, strict=True)` in `src/utils/goldenset_maintenance.py`, on the
correct grounds that `strict=` landed in 3.10 while the project declares 3.7+
(https://github.com/fasrc/archi/pull/192#discussion_r3713501203). That call was replaced with
`os.stat()`, so **PR #192 needs nothing from this change**. But the stale declaration is what
turned a non-issue into a P1, and it will keep manufacturing false P1s on every future PR that
reaches for a 3.9+ or 3.10+ API — while giving no protection at all against the real risk,
because nothing enforces the floor.

## What Changes

- Set `requires-python = ">=3.11"` in `pyproject.toml`, matching the pyright pin, CI, the
  container, and the `archi` conda env (3.11.15).
- Add `tests/unit/test_python_version_declaration.py` with **two** guards: the declared floor
  may not fall below the pyright target (red today), and the running interpreter must satisfy
  the declaration (guards the opposite drift).
- Correct the one surviving stale claim in prose: `docs/docs/adding_providers.md:244`
  (`- Python 3.7+`).

Explicitly **not** done: removing the `match` statements to restore 3.7 compatibility. The
declaration is what is wrong, not the code. Dependency pins and unrelated packaging fields are
out of scope for this change.

Two claims in the issue body did not survive re-derivation and are recorded here so the next
reader does not go looking: `CLAUDE.md` contains **no** Python version claim on current `dev`
(`grep -in python CLAUDE.md` is empty), and `pyproject.toml` has **no** `classifiers` list, so
there is no `Programming Language :: Python` trove classifier to keep in step.

## Capabilities

### New Capabilities
- `python-version-declaration`: the project's declared Python floor is the floor it is actually
  built, type-checked, and tested against, and a test fails when the declaration drifts from
  that reality in either direction.

### Modified Capabilities
<!-- None. No existing capability in openspec/specs/ governs packaging metadata. -->

## Impact

- **Packaging:** `pyproject.toml` — one line (`requires-python`). Installers on 3.7–3.10 will
  now refuse the package instead of installing it and failing at import. That is the intended
  behaviour change and the entire point.
- **Tests:** new `tests/unit/test_python_version_declaration.py`. Reads `pyproject.toml` with
  `tomllib` (stdlib on 3.11+) and compares specifiers with `packaging`, which pytest itself
  depends on, so no new declared dependency.
- **Docs:** `docs/docs/adding_providers.md` — one line.
- **No** change to `src/`. No runtime behaviour changes; nothing imports this metadata at run
  time. Because the repo measures diff coverage over `src/` only, a diff that touches no `src/`
  file reports "no lines with coverage information" and clears the 80% gate — that is expected
  here and is not a sign the gate was skipped.
- **No** change to CI, the container, or any deployment config. Those already run 3.11; this
  change makes the declaration agree with them rather than the other way round.
