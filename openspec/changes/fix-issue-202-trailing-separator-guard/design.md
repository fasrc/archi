## Context

`resolve_persisted_path(file_path, data_path)` (`src/utils/goldenset_maintenance.py:405`) is a
path-containment guard. `file_path` arrives from the document catalog or an operator-supplied
`--corpus-json` dump, and the file it names is read and shipped to an external model provider,
so the guard's whole job is to refuse anything it cannot vouch for. Its sole caller
(`scripts/benchmarking/goldenset_maintenance.py:669`) wraps it in `except ValueError` and turns
a refusal into a per-row `OperationalError`, so `ValueError` is the entire failure contract.

PR #192 (closing #183) established the principle that resolvability is the **kernel's** verdict
on the **stored pathname**, not something inferable from `Path.resolve()`'s output, because
resolution is lossy in both directions. The implementation of that principle is one `os.stat()`
call inside `_resolve_totally` (`src/utils/goldenset_maintenance.py:369`).

**The defect.** `_resolve_totally` is handed a `Path`, and `pathlib` normalizes in the
constructor — so the probe never receives the spelling the row stored. Reproduced on 3.11.15 in
this repo, with `safe.md` a real file under the root:

```
Path('safe.md/')   -> PosixPath('safe.md')    parts=('safe.md',)
Path('safe.md/.')  -> PosixPath('safe.md')    parts=('safe.md',)

os.stat('<root>/safe.md/')   -> NotADirectoryError errno 20
os.stat('<root>/safe.md/.')  -> NotADirectoryError errno 20
resolve_persisted_path('safe.md/',  str(root)) -> RETURNED <root>/safe.md
resolve_persisted_path('safe.md/.', str(root)) -> RETURNED <root>/safe.md
```

The guard therefore returns a readable regular file for a row whose pathname the OS refuses to
traverse. This is the same substitution as the two `..` erasures #183 fixed, reached through a
third erasing construct: the `Path` constructor rather than a later `..`.

Two things this is **not**. It is not a disclosure channel — `safe.md/` means `safe.md`, the
returned file is the one the row intended, and containment is not weakened. And it is not an
accident of the current code: the epoch-style tolerance was left in place deliberately when
#192 shipped, with the note that either enforcing the clause literally or documenting
trailing-separator tolerance would be defensible, but the current state is neither.

## Goals / Non-Goals

**Goals:**
- A `file_path` whose stored spelling the OS cannot traverse is refused by name with the guard's
  ordinary `ValueError`, closing the clause *"Only a path the operating system can actually
  traverse may be returned."*
- The probe sees the spelling the row wrote, for every spelling, not just the two the issue
  names — the fix is on the erasure mechanism, not on an enumeration of its symptoms.
- Spellings the OS *can* traverse keep resolving, including `./safe.md`, `web/./a.md`, and
  `web/../web/a.md`, which the current docstring explicitly promises still resolve.
- A `data_path` legitimately spelled with a trailing separator keeps working, decided
  deliberately and pinned by a test (the issue's step 4).

**Non-Goals:**
- Changing the ENOENT tolerance. Refusing paths whose target does not exist was considered and
  explicitly rejected in #192 — the corpus never prunes, so a nonexistent path is the normal
  stale-row case and that diagnostic belongs at the read. See
  https://github.com/fasrc/archi/pull/192#discussion_r3713656295 and
  `test_a_deleted_document_still_reaches_the_read_to_fail_there`, which must pass unmodified.
- Refusing a `file_path` that names a directory (`web/`). The kernel traverses it, containment
  holds, and it fails at the read with EISDIR — the same shape as the ENOENT case. Out of scope.
- Touching `_resolve_totally`'s single-raise-site design, its normalized reasons, or the `..`
  erasure gate.
- Auditing `Path()` construction anywhere else in the tree.

## Decisions

### Decision 1: Thread the raw spelling into the probe; do not validate before `Path()`

The issue sanctions two shapes: validate the raw string in `resolve_persisted_path` before
`Path(file_path)` is built, or thread the raw spelling through to the probe. **Thread it
through.**

`_resolve_totally` already owns the question "can the kernel traverse this pathname?" and
already owns the answer's wording, its single raise site, and its ENOENT tolerance. Giving it
one optional parameter — the spelling to probe, defaulting to `str(path)` so existing behavior
is unchanged — fixes the mechanism where it lives. A pre-`Path()` check in the caller would
instead put a second, parallel notion of untraversability in a second function, with its own
raise site and its own wording to keep in sync with the first.

It also generalizes correctly. The bug is not "trailing separators are not checked", it is "the
probe is fed a normalized rendering of the input". Once the probe sees the raw spelling, every
construct the constructor erases is covered by construction, including ones nobody enumerated.

### Decision 2: Refuse by the kernel's verdict, not by name

The issue's plan step 3 suggests refusing by name — "a trailing separator or a `.`/`..`
component in a persisted-document path is meaningless at best". Measurement says otherwise, so
this change refuses by verdict instead:

| raw spelling | `os.stat(raw)` | name-based rule | correct? |
|---|---|---|---|
| `safe.md/` | ENOTDIR | refuse | refuse |
| `safe.md/.` | ENOTDIR | refuse | refuse |
| `./safe.md` | OK | **refuse** | accept |
| `web/./a.md` | OK | **refuse** | accept |
| `web/../web/a.md` | OK | **refuse** | accept — the docstring promises it resolves |

A name-based rule over-refuses three traversable spellings, one of which the current docstring
explicitly documents as still resolving, and it would refuse `..` by name — which would break
`test_a_loop_erased_by_parent_traversal_is_refused` and
`test_a_missing_component_erased_by_parent_traversal_is_refused`, whose assertions pin the
*reasons* `symlink loop` and `a missing component erased by '..'`. Those reasons are produced by
the erasure gate downstream; a name-based pre-check would shadow it and silently make the #183
fix untested. Deciding by verdict changes nothing for any path the kernel accepts.

### Decision 3: `data_path` keeps its trailing separator — decided by the same verdict

The issue's step 4 asks for an explicit decision on the data root, since a hand-written config
plausibly ends in `/`. **A trailing separator on `data_path` stays legal**, and no special case
is needed to make it so: `os.stat()` accepts a trailing separator (and a trailing `.`) on a real
directory and rejects it on a non-directory, which is exactly the distinction wanted. Measured:

```
os.stat('<root>')   -> OK        os.stat('<root>/')  -> OK        os.stat('<root>/.') -> OK
```

So both arguments can be probed as spelled, symmetrically, with one rule. A name-based refusal
would have forced an asymmetric carve-out — refuse a trailing separator on `file_path`, permit
it on `data_path` — which is a rule about which argument you are, not about what the path is.
Pinned by a test, as the issue requires.

### Decision 4: Join a relative `file_path` to the root as a string

`resolve_persisted_path` currently builds the probe target with `root / candidate`. That is a
`pathlib` join, so it re-erases the trailing separator this change exists to preserve, and the
fix would be a no-op. The raw target must be composed with `os.path.join(str(root), file_path)`
for the relative case and be the raw string itself for the absolute case, with
`os.path.isabs(file_path)` deciding — not `candidate.is_absolute()`, which is again the
normalized reading. The resolved *output* still comes from `Path.resolve()` exactly as today;
only the probe input changes.

### Decision 5: One new refusal reason, phrased as the kernel phrases it

ENOTDIR arrives at the existing `except OSError` branch and is reported through the existing
`exc.strerror` path as `Not a directory`, so the message reads
`persisted document 'safe.md/' cannot be resolved: Not a directory`. No new reason string, no
new raise site, and the wording is identical on every interpreter because it is the same errno
everywhere (verified 3.11.15; `os.stat` semantics for a trailing separator on a non-directory
are POSIX, not interpreter-specific). ELOOP keeps its normalization to `symlink loop`.

## Risks / Trade-offs

- **[The probe target and the resolved output are now composed differently]** → Two expressions
  of the same join is a drift risk: someone could later "tidy" the string join into a `pathlib`
  one and silently revert the fix. Mitigation: a comment at the join saying why it is a string
  join, and the new tests fail the moment it is changed back — the mutation check in tasks.md
  step 5 proves that.
- **[A row spelled `web/` (a real directory) is still returned]** → The kernel traverses it, so
  by Decision 2 it is accepted and fails at the read with EISDIR. That is consistent with the
  ENOENT tolerance and is called out as a non-goal rather than left unstated. Refusing
  directories is a separate, defensible change and is not folded in here.
- **[ENOENT still hides an untraversable spelling in one case]** → `missing/` probes ENOENT, not
  ENOTDIR, so it is tolerated and reaches the read. This is the deliberate stale-row tolerance
  and the constraint forbids narrowing it; the erasure gate already covers the dangerous version
  of this (`missing/../safe.md`), where a `..` could have erased the missing component.
- **[Windows spellings]** → `os.stat` on a trailing separator is POSIX-specified; the project
  runs and gates on Linux only, and the guard already assumes POSIX semantics elsewhere (the
  #183 delta says so explicitly). No new platform assumption.
- **[Adding a parameter to a module-private helper]** → `_resolve_totally` has one other caller
  (the data root) and is module-private, so the signature change is contained. It defaults to
  today's behavior, so the data-root call site could be left untouched; passing the raw
  `data_path` there is what buys Decision 3's symmetry and its test.

## Open Questions

None. The two judgment calls the issue flagged are decided above with measurements: the
`data_path` trailing separator (Decision 3) and enforce-vs-document (Decision 2 — enforce, by
verdict rather than by name).
