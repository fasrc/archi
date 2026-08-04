## Why

`resolve_persisted_path` in `src/utils/goldenset_maintenance.py:283` is the path-containment
guard that decides whether a persisted corpus document may be read and shipped to an external
model provider. It promises exactly one failure mode — `ValueError` — and its sole caller
(`scripts/benchmarking/goldenset_maintenance.py:669`) converts that into a per-row
`OperationalError`. But the guard resolves paths with `Path.resolve()`, which on this repo's
interpreter (Python 3.11.15, confirmed: `RuntimeError: Symlink loop from '<path>'`) raises
`RuntimeError` rather than returning a path when it meets a symlink loop. That `RuntimeError`
escapes the documented contract, so one malformed corpus row ends the entire maintenance run
on a traceback instead of being refused by name.

## What Changes

- Make path resolution in `resolve_persisted_path` **total**: a symlink loop on either side of
  the containment comparison becomes the module's ordinary `ValueError` refusal, naming the
  offending row (or the data root) so the operator can find it.
- Keep `Path.resolve()` as the resolver and convert `RuntimeError` to `ValueError`, rather than
  switching to `os.path.realpath`. `realpath()` on a self-referential link returns the loop path
  *itself* — a path that does not exist but which **is** inside the data root, so containment
  would pass and the row would be silently accepted, failing later at `read_text()`. Refusing a
  symlink loop by name is the correct answer for a security check; resolving it to something
  readable-looking is not.
- Record that reasoning in the docstring, which already explains why both sides are resolved.
- Cover both unguarded `.resolve()` calls (the data root at line 305 and the candidate at line
  307), so no unguarded call remains.
- Add tests: a self-referential `file_path` under the data root, and a data root that is itself
  a symlink loop. Existing escape-the-root containment tests must keep passing — making
  resolution total must not make the check blind.

No behavior changes for any path that resolves successfully. Not a breaking change: it converts
an uncaught crash into an already-handled exception type.

## Capabilities

### New Capabilities
- `ragas-goldenset-maintenance`: adds the path-containment requirement for persisted corpus
  documents — the guard's totality contract (every containment failure, including an
  unresolvable path, surfaces as a `ValueError` naming the row). No requirement anywhere in
  `openspec/specs/` currently specifies this containment behavior, so this is an addition
  rather than a modification.

### Modified Capabilities
<!-- None. No existing archived capability specifies path containment for persisted documents. -->

## Impact

- `src/utils/goldenset_maintenance.py` — `resolve_persisted_path` body and docstring only.
- `tests/unit/test_goldenset_maintenance.py` — new cases alongside the existing containment
  tests at lines 1207-1253.
- Caller `scripts/benchmarking/goldenset_maintenance.py:669` is **unchanged**: it already
  catches `ValueError`, which is precisely why routing the new failure through `ValueError`
  needs no caller edit.
- No new dependencies. No config, CLI, or public API surface changes, so no docs update is
  required.
