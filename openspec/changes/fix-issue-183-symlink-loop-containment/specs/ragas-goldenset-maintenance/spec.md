## ADDED Requirements

### Requirement: Persisted-document path resolution is total and contained

`resolve_persisted_path(file_path, data_path)` SHALL decide whether a persisted corpus
document may be read, and SHALL be **total**: every input either returns a path contained
under the resolved data root, returns `None` (no path on the row), or raises `ValueError`.
It MUST NOT raise any other exception type for a malformed or hostile `file_path`, and MUST
NOT return a path it could not fully resolve.

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

#### Scenario: The caller reports the refusal per row and continues to be able to run

- **WHEN** the containment guard raises `ValueError` for one corpus row
- **THEN** the caller's `except ValueError` converts it to an `OperationalError` carrying the
  same message
- **AND** no traceback from path resolution reaches the operator
