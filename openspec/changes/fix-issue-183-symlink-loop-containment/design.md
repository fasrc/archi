## Context

`resolve_persisted_path` (`src/utils/goldenset_maintenance.py:283`) is a path-containment
security guard. `file_path` arrives from the document catalog or an operator-supplied
`--corpus-json` dump; the named file is read and its text sent to an external model provider,
so a `..` traversal, an absolute path, or a symlink pointing out of the data root is a
file-disclosure channel off the machine. The guard resolves both the root and the candidate,
compares by path component, and raises `ValueError` on any containment failure.

Its sole caller is `scripts/benchmarking/goldenset_maintenance.py:669`:

```python
try:
    path = resolve_persisted_path(doc.file_path, data_path)
except ValueError as exc:
    raise OperationalError(str(exc)) from exc
```

So `ValueError` is the entire failure contract — one row's refusal, reported and survivable.

**The defect.** Resolution is not total. Verified on this repo's interpreter
(Python 3.11.15):

```
>>> Path(loop).resolve()
RuntimeError: Symlink loop from '/tmp/.../loop'
>>> os.path.realpath(loop)
'/tmp/.../loop'          # the loop path itself — which does not exist
```

`RuntimeError` is not `ValueError`, so it sails past the caller's handler and one malformed
corpus row ends the whole maintenance run on a traceback. Two `.resolve()` calls are exposed:
the data root (line 305) and the candidate (line 307).

**Version sensitivity, which the issue did not account for.** The `RuntimeError` is
pre-3.13 pathlib behavior. From Python 3.13, `Path.resolve()` is implemented over
`os.path.realpath(strict=False)` and no longer raises on a loop — it returns the loop path.
Any fix that only catches `RuntimeError` is therefore correct on 3.11/3.12 and silently
wrong on 3.13+, where the loop path (which *is* inside the data root) would pass containment
and be returned to be read. Making the guard total must not make it version-dependent.

Related precedent: the aliasing guard in `scripts/benchmarking/goldenset_maintenance.py` hit
the same `Path.resolve()` exposure and was fixed under PR #162 (`7b210b7f`) by resolving
through `os.path.realpath`. That fix is not transferable here — see Decision 1.

## Goals / Non-Goals

**Goals:**
- Every input to `resolve_persisted_path` returns a contained path, returns `None`, or raises
  `ValueError`. No other exception type escapes.
- A symlink loop is refused **by name**, never returned and never resolved to something
  read-looking.
- Correct on every Python version this repo supports, not just the current 3.11.
- Existing containment coverage (escape-the-root, sibling-root, `..`, absolute) keeps passing
  unchanged.

**Non-Goals:**
- Changing the caller. It already catches `ValueError`; routing the new failure through
  `ValueError` is what makes the caller edit unnecessary.
- Reworking the aliasing guard in `scripts/benchmarking/goldenset_maintenance.py` (already
  fixed under PR #162), or auditing `.resolve()` anywhere else in the tree. This change is one
  function, its docstring, and its tests.
- Tolerating a symlink loop by reading through it. There is nothing to read.

## Decisions

### Decision 1: Refuse unresolvable paths; do NOT switch to `os.path.realpath`

The issue offered two options: swap in `os.path.realpath` (total, matching the PR #162 fix),
or keep `Path.resolve()` and convert `RuntimeError` into the same `ValueError` refusal. **Take
the second.**

The precedent does not carry over because the two call sites use their resolved paths
differently. In the aliasing guard the resolved paths are only compared for *equality*, so
swapping the resolver is semantically free. Here the resolved path is **returned to be read**.
`realpath()` on a self-referential link returns the loop path itself — a path that does not
exist, but which *is* inside the data root. Containment would therefore **pass**, the guard
would hand back a path, and the failure would surface later and elsewhere as
`OperationalError("cannot read the persisted document ...")` from `read_text()`.

That is strictly worse in two ways: the diagnostic no longer names the real problem, and a
security guard whose job is to refuse would have *accepted* a path whose target is unknown.
For a containment check, "I cannot resolve this, so I refuse it" is the correct and clearer
answer. Refusing is also the conservative direction: a symlink loop is never a legitimate
persisted document.

### Decision 2: Detect unresolvability by outcome, not only by exception

Because `Path.resolve()` raises on 3.11/3.12 but returns the loop path on 3.13+, catching
`RuntimeError` alone is a version-specific fix. Treat "resolution did not terminate" as the
condition, established two ways that together cover both behaviors:

- `RuntimeError` from `.resolve()` (3.11/3.12), and
- a returned path that is *still a symlink* (3.13+). A fully-resolved path is never a symlink,
  so `resolved.is_symlink()` is a sound post-condition check and is the only case it can catch.

Both routes must converge on **one shared refusal** so the message and type are identical
regardless of interpreter.

### Decision 3: One helper, both `.resolve()` calls, one raise site

Route the root and the candidate through a single module-private helper rather than wrapping
each call inline. Three reasons:

- The acceptance criteria require every remaining `.resolve()` to be guarded or justified; a
  helper guards both by construction and leaves no unguarded call to justify.
- The root and the candidate need *different* messages (naming the data root vs. naming the
  row), which a helper takes as a parameter while keeping one `raise` statement.
- A single raise site keeps patch coverage full. The gate measures **line** coverage on
  changed lines (`diff-cover --fail-under=80`; `pytest --cov` runs without `--cov-branch`), so
  a shared raise executed by the 3.11 `RuntimeError` test covers that line even though the
  `is_symlink()` route cannot fire on this interpreter. Two separate inline raises would leave
  the 3.13-only one permanently uncovered.

### Decision 4: Record the reasoning in the docstring

The docstring already explains why both sides are resolved and why containment is "not
optional politeness". Extend it with why an unresolvable path is refused rather than resolved.
This is the third reader in a row to meet this function (PR #162 review, this issue, the next
person); the reasoning belongs next to the code, not only in this design doc.

## Risks / Trade-offs

- **[The `is_symlink()` route is unexercised on Python 3.11]** → It is dead code on this
  interpreter and cannot be tested here without mocking `Path.resolve()`. Mitigation: keep it
  to a single condition folded into the shared refusal (Decision 3) so it costs no coverage
  and cannot drift out of sync with the `RuntimeError` route; the comment states plainly which
  interpreter each route serves. Optionally assert it directly by patching `Path.resolve` to
  return the loop path — a forward-compat test, cheap and honest about what it simulates.
- **[Refusing is a behavior change for a caller that previously crashed]** → Callers cannot be
  relying on the `RuntimeError`; it aborted the run. Converting a crash into an
  already-handled exception type strictly widens what the tool survives. Not breaking.
- **[A data root that is itself a symlink loop is operator misconfiguration, not a bad row]**
  → Refusing it as `ValueError` still routes through the caller's handler and produces a named
  message rather than a traceback, which is the outcome that matters. Distinguishing it as a
  separate error type would require a caller change, which is out of scope.
- **[Making resolution total could make containment blind]** → This is the failure mode the
  issue explicitly warns about. Mitigation: the existing escape-the-root, `..`, absolute-path
  and sibling-root tests must pass **unmodified**, and the mutation check (revert the fix,
  confirm exactly the new tests fail) proves the new tests are the only thing the fix moves.

## Open Questions

None. The resolver choice (Decision 1) and the `*/*`-style contract ambiguity that deferred
other spin-offs from PR #162 do not arise here: the refusal type is already fixed by the
caller's `except ValueError`.
