## Context

`tests/smoke/test_embedding_benchmarks.py` holds two different kinds of thing in one file:

1. **Pure classification** — given an exception object, does it mean "no usable answer came back"?
   `_NETWORK_ERROR_TYPES` (`:66`, built by four conditional-import blocks), `_HUB_HTTP_ERROR`
   (`:86`), `_NETWORK_ERRNOS` (`:123`), `_TRANSIENT_4XX_STATUSES` (`:144`), `_is_transient_status`
   (`:147`), `_GUARDED_ERRORS` (`:168`), `_is_network_failure` (`:171`), plus the import helper
   `_import_or_skip` (`:238`) and the two test helpers `_response` (`:209`) and `_assert_propagates`
   (`:217`). None of it imports `langchain_huggingface`; none of it opens a socket.
2. **Integration with the real library** — `_load_model` (`:263`, imports
   `langchain_huggingface`), `TestEmbeddingBenchmarks` (`:284`, downloads ~90 MB of weights),
   `TestEmbeddingGuard` (`:450`, 24 tests that monkeypatch
   `langchain_huggingface.HuggingFaceEmbeddings.__init__` by string), and
   `TestTheGuardTestsDegradeLikeTheCodeTheyGuard` (`:375`, which re-runs the file in a subprocess
   with the library blocked).

The gate collects `tests/unit` only (`pyproject.toml:74`), so category 1 — the part that can be
wrong in a way nothing else notices — is unchecked by every pull request. #187 put the file in
`tests/smoke/` on purpose and that decision stands: the fix is to gate the logic, not to relocate
the tests that need the library.

Measured on this branch's base (`origin/dev` @ `0a157cdc`):

```
python -m pytest tests/unit/ --collect-only -q | grep -ci embedding_guard   # 0
```

Of the 24 tests in `TestEmbeddingGuard`, exactly one —
`test_no_named_network_type_drags_in_a_local_defect` — touches neither the library nor a
monkeypatch. It is the test #187's spec requires ("The allowlist SHALL be enforced by a test that
inspects the subclasses of every named type"), and it is ungated.

## Goals / Non-Goals

**Goals**

- A pull request that widens the allowlist, drops an exception family, or breaks `_import_or_skip`
  fails `bash scripts/gate.sh`.
- The gated tests import no embedding library and reach no network.
- The classification logic has exactly one definition in the tree.
- The deliberate smoke suite keeps working unchanged, including under a missing
  `langchain_huggingface`.

**Non-Goals**

- Promoting the guard into `src/`. It is test infrastructure until something ships that reuses it.
- Moving `TestEmbeddingGuard` into the gate. Its monkeypatching needs the library.
- Changing `testpaths`, the gate script, or any workflow. Out of scope by the issue, and forbidden
  by the nightly rails.
- Re-litigating the allowlist's membership. This change moves and gates the existing rules; it does
  not add or remove an exception family.

## Decisions

### D1. Extract the pure layer into `tests/support/embedding_guard.py`; do not move tests

Resolved with the operator on 2026-08-10 (issue #200, "Option B"). The alternatives were:

- *Move the guard tests to `tests/unit/`* — rejected: 23 of 24 resolve a monkeypatch target by
  string, so the gate would import `langchain_huggingface` (~2s per run) and acquire a dependency
  #187 deliberately kept out of it.
- *Add a second `pytest` invocation for `tests/smoke/` to the gate* — rejected: it edits the control
  plane, and it puts network-touching tests back on the critical path of every pull request, which
  is the exact defect #187 fixed.
- *Duplicate the classifier into a unit test* — rejected: two definitions drift, and the copy the
  gate checks would not be the copy the smoke suite runs.

Extraction gives the gate the real object under test with no library import.

### D2. The seam is "needs no library and no socket", which is wider than the five symbols the issue names

The issue lists `_NETWORK_ERROR_TYPES`, `_NETWORK_ERRNOS`, `_GUARDED_ERRORS`, `_is_network_failure`
and `_import_or_skip`. Three more must travel with them or the module does not import:
`_HUB_HTTP_ERROR` and the four conditional-import blocks that build `_NETWORK_ERROR_TYPES`,
`_TRANSIENT_4XX_STATUSES`, and `_is_transient_status` — `_is_network_failure` calls the last one on
its first branch, which is the branch that distinguishes a 404 from a 503 and therefore the one most
worth gating.

`_assert_propagates` and `_response` also move. They are test helpers, not classifier internals, but
every negative test in either suite needs `_assert_propagates` — it is what turns "the guard
swallowed this into a skip" from a green SKIPPED into a red failure, and a negative test written
with a bare `pytest.raises` instead would report green in exactly the case it exists to catch.
Leaving it behind would mean writing it twice.

The conditional-import structure is preserved verbatim, including each `except ImportError: pass`,
so the module still imports in a minimal environment and the tuple still reflects what is installed.

### D3. Unit and smoke both import `from tests.support.embedding_guard import ...`

`tests/` has no `__init__.py` (`tests/unit/` does), so `tests` resolves as an implicit namespace
package and `tests.support` needs the repository root on `sys.path`. It is there under every
invocation this change relies on:

- the gate runs `python -m pytest tests/unit/`, and `-m` inserts the current directory;
- the documented smoke command is `python -m pytest tests/smoke/test_embedding_benchmarks.py`, same
  mechanism;
- the subprocess inside `TestTheGuardTestsDegradeLikeTheCodeTheyGuard` passes
  `cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__)))` (`:437`) — the repository root —
  and invokes `sys.executable -m pytest`, so the child resolves the import too. This is the one
  place extraction could plausibly have broken the smoke suite, and it does not.

`tests/support/__init__.py` is added so the package is explicit rather than relying on namespace
resolution. A bare `pytest` (no `-m`) from the repository root is **not** relied upon; task 5
verifies both documented commands rather than assuming.

### D4. The allowlist inspection is added to the unit suite; the smoke copy stays

`test_no_named_network_type_drags_in_a_local_defect` is pure, so a gated equivalent is what makes
acceptance criterion 1 ("widening the classifier fails the gate") true. Two options:

- *Move the method out of `TestEmbeddingGuard`* — rejected, narrowly. The issue's out-of-scope list
  keeps that class in smoke, and the smoke suite is run deliberately by a developer debugging the
  embedding path, who should not have to run a second suite to learn the allowlist is malformed.
- **Chosen:** the unit suite gets the authoritative inspection test; the smoke method stays where it
  is, now inspecting the *imported* `_NETWORK_ERROR_TYPES`. Both assert against one definition, so
  they cannot disagree about what the allowlist contains — the duplication is two call sites of the
  same invariant, not two copies of the rule. Cost is microseconds and no import.

### D5. Gated status tests use a synthetic response stub, not `requests`

`_is_network_failure` reads the status through `getattr(getattr(exc, "response", None),
"status_code", None)`, so a `types.SimpleNamespace(status_code=503)` attached to a synthetic
exception exercises the branch exactly as a real `requests.Response` would. The unit suite therefore
needs no third-party import at all, which is a stronger hermeticity property than "does not import
`langchain_huggingface`". `_response` still moves to the shared module because the smoke tests use
it, and it keeps its `pytest.importorskip("requests")`.

### D6. Mutation is the acceptance test, and it is a task, not a note

An assertion that the new tests pass proves nothing about whether they *bind*. Task 6 breaks
`_is_network_failure` to `return True`, runs the gate, records that it goes red, and reverts —
and does the same for a dropped exception family and a swallowing `_import_or_skip`. A gated test
that cannot fail is the defect this change exists to remove; shipping one would be ironic rather
than merely useless.

### D7. Diff coverage is expected to be a no-op, with a stated fallback

`coverage.xml` on this base records `<source>/workspace/src</source>`, so files under `tests/` are
not measured and a tests-only diff contributes no coverable changed lines. The ≥80% diff-coverage
gate should therefore pass trivially, as it did for #187 (also tests-only). If the gate instead
reports 0% on the new files, the fallback is to confirm the coverage scope with the gate's own
output and report it in the pull request rather than to widen the coverage configuration — that file
is control plane and this change does not touch it.

## Risks / Trade-offs

- **Underscore-prefixed names cross a module boundary.** `from tests.support.embedding_guard import
  _is_network_failure` imports a private name, which reads oddly. Renaming to public names would
  make the smoke diff a rename of ~40 call sites and bury the one behavioural change under churn.
  Kept private; the module docstring states it is a test-support seam shared by two suites, and a
  later rename is mechanical.
- **Two suites now share a module, so a careless edit to it can red the gate.** That is the point.
- **The four `except ImportError: pass` lines stay uncovered** in an environment where all optional
  libraries are installed. They are not measured (D7) and asserting on them would mean simulating
  four absent libraries to prove a `pass` executes.
- **The smoke file's line anchors all shift** when ~180 lines leave it. No document references them
  except this change and issue #200; `docs/` mentions the smoke command, not line numbers.

## Migration Plan

None. No `src/` file, schema, config, or deployment artifact changes; nothing is versioned or
persisted. The change is complete when the gate collects the new module and mutation reds it.
