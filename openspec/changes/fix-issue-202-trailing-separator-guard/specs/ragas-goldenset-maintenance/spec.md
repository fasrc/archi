## MODIFIED Requirements

### Requirement: Persisted-document path resolution is total and contained

`resolve_persisted_path(file_path, data_path)` SHALL decide whether a persisted corpus document may be read, and SHALL be **total**: every input either returns a path contained under the resolved data root, returns `None` (no path on the row), or raises `ValueError`. It MUST NOT raise any other exception type for a malformed or hostile `file_path`, and MUST NOT return a path it could not fully resolve.

`ValueError` is the guard's whole failure contract: the sole caller wraps the call in
`except ValueError` and converts it to a per-row `OperationalError`, so any other exception
type escapes that handling and aborts the entire maintenance run on a traceback instead of
refusing one row.

Every refusal message MUST name the offending `file_path` (or the data root, when the root
itself is what could not be resolved), so an operator can locate the bad row without a
stack trace.

Totality MUST NOT be bought by weakening containment. A path that cannot be fully resolved
— in particular a symlink loop, which no resolver can reduce to a real file — SHALL be
refused by name rather than returned, even when the unresolvable path would itself compare
as inside the data root. `file_path` arrives from the catalog or from an operator-supplied
`--corpus-json` dump, and the file it names is read and sent to an external model provider,
so accepting a path whose target is unknown is a file-disclosure channel.

The requirement MUST hold independently of the interpreter's `Path.resolve()` behavior for
unresolvable paths, which differs across supported Python versions.

Resolvability SHALL be decided by probing the stored pathname itself, never inferred from the
resolved output, because resolution is lossy in both directions: a later `..` erases the
failing component the resolver stepped over, and on some interpreters a component that could
not be inspected at all is indistinguishable from one that is simply not a symlink. Only a
path the operating system can actually traverse may be returned. The probe MUST NOT depend on
an API newer than the project's declared Python floor.

The probed pathname SHALL be the spelling the row stored, character for character, and never a
normalized rendering of it. Path construction itself erases traversability-bearing syntax before
any probe can see it — `Path('safe.md/')` and `Path('safe.md/.')` both yield `PosixPath('safe.md')`
with parts `('safe.md',)` — so a probe fed a constructed path reports on a pathname the row did
not store. Where a relative `file_path` is joined to the data root to form the probe target, that
join SHALL preserve the stored spelling, which a path-object join does not.

Traversability SHALL be the operating system's verdict on that spelling, not a judgement about
which characters the spelling contains. `./a.md`, `web/./a.md` and `web/../web/a.md` are all
traversable when their components are real and MUST still resolve; refusing them by name would
also shadow the `..` erasure gate above, whose refusals are the only thing distinguishing an
erased missing component from a real one.

A path whose target does not exist is the single tolerated failure, so that a stale row
reaches containment and then fails at the read where that diagnostic belongs. That tolerance
SHALL be withheld whenever the spelling contains `..`, because normalization can erase the
very component that was missing — the gate is on the erasing construct, not on the particular
error it hid.

#### Scenario: A relative path under the data root resolves

- **WHEN** `file_path` is a relative path such as `web/docs/a.md` and `data_path` is the data root
- **THEN** the function returns the root joined with that path, fully resolved

#### Scenario: A row with no path is not an error

- **WHEN** `file_path` is empty
- **THEN** the function returns `None` and raises nothing

#### Scenario: A path escaping the data root is refused

- **WHEN** `file_path` resolves outside the data root (an absolute path elsewhere, a `..`
  traversal, or a symlink inside the root whose target is outside it)
- **THEN** the function raises `ValueError` naming that `file_path` and the data root
- **AND** returns no path, so nothing is read

#### Scenario: A sibling root is not mistaken for a child

- **WHEN** `file_path` resolves to a path under a directory whose name merely shares a prefix
  with the data root, such as `/srv/data-old/x.md` against the root `/srv/data`
- **THEN** the function raises `ValueError` — containment is compared by path component

#### Scenario: A symlink loop in the document path is refused, not crashed

- **WHEN** `file_path` names a self-referential symlink under the data root, so resolution
  cannot terminate
- **THEN** the function raises `ValueError` naming that `file_path` as unresolvable
- **AND** does NOT raise `RuntimeError` or any other type
- **AND** does NOT return the loop path, even though that path lies inside the data root

#### Scenario: A symlink loop in the data root is refused, not crashed

- **WHEN** `data_path` itself names a symlink loop, so the data root cannot be resolved
- **THEN** the function raises `ValueError` naming the data root as unresolvable
- **AND** does NOT raise `RuntimeError` or any other type

#### Scenario: A loop the path's own `..` erased is refused

- **WHEN** `file_path` is `loop/../safe.md`, `loop` is a symlink loop, and `safe.md` is a real
  readable file under the data root — so the operating system cannot traverse the stored
  pathname, yet the resolver collapses the `..` and reports `<root>/safe.md`
- **THEN** the function raises `ValueError` naming that `file_path` as unresolvable
- **AND** does NOT return the collapsed path, which is contained and readable but is not the
  file whose pathname the row actually stored

#### Scenario: A missing component erased by parent traversal is refused

- **WHEN** `file_path` is `missing/../safe.md`, `missing` does not exist, and `safe.md` is a
  real readable file under the data root — so the pathname cannot be traversed, yet
  normalization collapses it to `<root>/safe.md`
- **THEN** the function raises `ValueError` naming that `file_path` as unresolvable
- **AND** the missing-target tolerance does NOT apply, because the `..` erased the component
  that was missing

#### Scenario: A trailing separator path construction erased is refused

- **WHEN** `file_path` is `safe.md/` and `safe.md` is a real readable file under the data root —
  so the stored pathname is untraversable (`ENOTDIR`), yet path construction erases the trailing
  separator before any probe can see it
- **THEN** the function raises `ValueError` naming that `file_path` as unresolvable
- **AND** does NOT return `<root>/safe.md`, which is contained and readable but is not the
  pathname the row stored

#### Scenario: A trailing `.` component path construction erased is refused

- **WHEN** `file_path` is `safe.md/.`, which path construction also collapses to `safe.md`, and
  the stored pathname is likewise untraversable
- **THEN** the function raises `ValueError` naming that `file_path`
- **AND** the refusal reads the same as for the trailing separator, both being one errno from
  one probe of the stored spelling

#### Scenario: A traversable `.` or `..` in the spelling still resolves

- **WHEN** `file_path` is `./safe.md`, `web/./a.md`, or `web/../web/a.md` and every component is
  real, so the operating system traverses the stored pathname without complaint
- **THEN** the function returns the contained resolved path
- **AND** the spelling is NOT refused for containing `.` or `..`, because traversability is the
  operating system's verdict and not a property of the characters

#### Scenario: A data root spelled with a trailing separator is accepted

- **WHEN** `data_path` is a configured directory spelled `/srv/archi/data/` — legitimate for a
  directory, and accepted by a probe of that spelling because the target is a real directory
- **THEN** the data root resolves and a contained `file_path` under it still returns
- **AND** the trailing separator is not refused on the root, the same probe rule serving both
  arguments without a per-argument exception

#### Scenario: A deleted document still reaches the read

- **WHEN** `file_path` names a file that no longer exists but whose spelling contains no `..`,
  so nothing can have been erased
- **THEN** the function returns the contained resolved path
- **AND** the failure surfaces at the read as a per-row error, not as a refusal here

#### Scenario: A component the resolver could not inspect is refused

- **WHEN** a component of `file_path` cannot be probed — an unreadable parent directory, an
  overlong name — and the interpreter reports that by giving up silently rather than raising
- **THEN** the function raises `ValueError` naming the path as unresolvable
- **AND** does NOT treat "the probe reported no symlink" as proof that resolution completed

#### Scenario: A symlink swapped in after resolution is refused

- **WHEN** resolution gives up on an unresolvable component and that component is replaced by
  a symlink pointing outside the data root before the traversability probe runs, so the probe
  itself succeeds
- **THEN** the function raises `ValueError` rather than returning the path
- **AND** it does NOT rely on containment to catch this, which compares the path as spelled and
  would accept it

#### Scenario: A malformed `file_path` is refused by name

- **WHEN** `file_path` contains something the resolver rejects outright, such as an embedded
  NUL, so it raises `ValueError` itself before the guard inspects anything
- **THEN** the refusal names the offending `file_path` and identifies it as a persisted
  document, rather than surfacing the resolver's own bare message
- **AND** the reason reads the same on every interpreter, whose native wording differs

#### Scenario: The caller reports the refusal per row and continues to be able to run

- **WHEN** the containment guard raises `ValueError` for one corpus row
- **THEN** the caller's `except ValueError` converts it to an `OperationalError` carrying the
  same message
- **AND** no traceback from path resolution reaches the operator
