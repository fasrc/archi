## Why

`resolve_persisted_path` (`src/utils/goldenset_maintenance.py:405`) is the containment guard
that vets every `file_path` from the document corpus before the file is read and its text sent
to an external model provider. Since PR #192 (closing #183) it decides *resolvability* by
probing the stored pathname with `os.stat()` inside `_resolve_totally`
(`src/utils/goldenset_maintenance.py:369`) — one syscall asking the kernel to traverse exactly
the pathname the row stored.

Except it does not get the pathname the row stored. `_resolve_totally` takes an
already-constructed `Path`, and `pathlib` erases a trailing separator and a `.` component **in
the constructor**, before the probe can see them. Measured on 3.11.15 in this repo:

```
Path('safe.md/')  -> PosixPath('safe.md')   parts=('safe.md',)
Path('safe.md/.') -> PosixPath('safe.md')   parts=('safe.md',)

os.stat('<root>/safe.md/')            -> ENOTDIR errno 20   # the kernel rejects it
resolve_persisted_path('safe.md/', str(root)) -> <root>/safe.md   # the guard accepts it
```

So for a row spelled `safe.md/` the guard hands back a readable regular file while the pathname
the row actually stored is untraversable on POSIX. This is the third mechanism of the one defect
class PR #192 chased across three review rounds — *the guard certifies a path as resolved when
the original spelling was not traversable*. The first two erasures were done by a later `..`
(fixed in `94ff83b9` and `267395df`); this one is done by the `Path` constructor.

It is a spec-conformance defect, not a disclosure hole: `safe.md/` unambiguously *means*
`safe.md`, so containment still holds and the returned file is the one the row intended. What it
violates is the clause PR #192 added — *"Only a path the operating system can actually traverse
may be returned"* — which the current state neither enforces nor documents as tolerated.
Origin: https://github.com/fasrc/archi/pull/192#discussion_r3713656282.

## What Changes

- Give the probe the **raw stored spelling** instead of a `pathlib`-normalized rendering of it,
  so `os.stat()` sees `safe.md/` as `safe.md/` and reports ENOTDIR. `_resolve_totally` grows one
  optional parameter for the spelling to probe, defaulting to today's behavior; the single raise
  site, the normalized reasons, and the ENOENT tolerance are all untouched.
- Join a relative `file_path` to the data root **as a string**, because `root / candidate`
  re-erases the trailing separator the fix exists to preserve.
- Refuse by the kernel's verdict rather than by name. A name-based rule (reject a trailing
  separator or a `.` component) would also refuse `./safe.md` and `web/./a.md`, which the OS
  traverses perfectly well, and it would need an asymmetric carve-out for `data_path` — see
  design.md Decision 2.
- Settle the `data_path` question the issue's step 4 raises: a config value spelled
  `/srv/archi/data/` stays legal, because `os.stat()` accepts a trailing separator on a real
  directory. Pinned by a test either way, per the issue.
- Add tests for `safe.md/` and `safe.md/.` beside `test_a_loop_erased_by_parent_traversal_is_refused`,
  plus tests proving the traversable spellings (`safe.md`, `./safe.md`, `web/../web/a.md`) and
  the ENOENT tolerance (`web/deleted.md`) are unchanged.

No behavior change for any spelling the kernel can traverse. The ENOENT tolerance, the `..`
erasure gate, and the containment comparison are all left exactly as they are.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `ragas-goldenset-maintenance`: the requirement *"Persisted-document path resolution is total
  and contained"* already says resolvability must be decided by probing the stored pathname
  itself. This change tightens *stored pathname* to mean the spelling as the row wrote it, not
  a normalized rendering, and adds scenarios for the erased trailing separator and trailing `.`.
  The base requirement was added by change `fix-issue-183-symlink-loop-containment`, which is
  merged but **not yet archived**, so it still lives in that change's delta rather than in
  `openspec/specs/ragas-goldenset-maintenance/spec.md` — this delta must archive after it.

## Impact

- `src/utils/goldenset_maintenance.py` — `_resolve_totally` signature/probe line plus its
  docstring, and the two call sites in `resolve_persisted_path`. No other function.
- `tests/unit/test_goldenset_maintenance.py` — new cases in `TestPersistedDocumentPath`
  (class at line 1204). Both files are black- and isort-clean today, so in-place edits will not
  be reflowed by the gate's writer-mode formatter.
- Caller `scripts/benchmarking/goldenset_maintenance.py:669` is **unchanged**: the new refusal
  is the same `ValueError` it already converts to a per-row `OperationalError`.
- No new dependencies, no config, CLI, or public API surface. `os.stat` has no version floor,
  so no Python-version sensitivity is introduced.
