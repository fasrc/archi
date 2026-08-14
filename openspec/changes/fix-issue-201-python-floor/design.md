# Design — declare the real Python floor

## Decision 1: the floor is `>=3.11`, not `>=3.10`

The strictest *syntax* requirement measurable in the tree is 3.10 (the `match` statements at
`src/bin/service_benchmark.py:804,864`). So `>=3.10` is defensible on a literal reading of
"what does the code need".

It is rejected anyway, because declaring `>=3.10` would repeat the original mistake in
miniature: it claims support for an interpreter that **no CI job, no container, and no
developer environment has ever executed**, and that pyright — pinned to 3.11 — would never
check. A 3.10-incompatible API added tomorrow would land silently under a `>=3.10` declaration
exactly as `strict=` did under `>=3.7`.

`>=3.11` is the only floor the project can currently make a *true* statement about: CI runs it,
pyright checks it, the container ships it, and the `archi` conda env is 3.11.15. If 3.10 support
is ever genuinely wanted, the honest way to get it is a CI matrix entry, and then this
declaration follows the matrix rather than leading it.

## Decision 2: the guard that goes red is the internal contradiction, not the issue's

The issue proposes a test that reads `requires-python` and asserts the running interpreter
satisfies it. That test is worth having, but on its own it is **not a valid TDD red step**:
3.11 already satisfies `>=3.7`, so it passes against the unfixed tree. It detects only the
declaration drifting *ahead* of the interpreter (`>=3.99`), never the actual defect, which is
the declaration lagging *behind* it.

The assertion that fails today is the contradiction already sitting inside `pyproject.toml`:

    requires-python  = ">=3.7"    # line 5   — what we promise the world
    pythonVersion    = "3.11"     # line 78  — what we actually type-check

The invariant is "the project SHALL NOT claim to support an interpreter it does not
type-check against", i.e. `floor(requires-python) >= pythonVersion`. Red today (3.7 < 3.11),
green after the one-line fix, and it keeps failing for any future regression of the same shape.

It also has a practical advantage over deriving the floor from CI: both operands live in
`pyproject.toml`, so the test needs no YAML parser and no read of `.github/workflows/**`,
which is a protected control-plane path under this repo's automation rails.

Both guards ship. They are complementary — one bounds the declaration from below, the other
from above — and together they pin it to a single interpreter series.

## Decision 3: a new test file, not `tests/unit/test_repo_hygiene.py`

`test_repo_hygiene.py` is the closest existing neighbour and was the first candidate. It is
rejected on two counts. Its module docstring scopes it to invariants that "deliberately inspect
the REAL git repository", which this one does not — it inspects a file. More concretely, it
carries a module-level `pytestmark = pytest.mark.skipif(not a git checkout)`; inheriting that
would silently skip the version guard anywhere the repo is consumed as an unpacked sdist, which
is precisely a context where a wrong `requires-python` matters most.

## Decision 4: `packaging` is an acceptable test-only import

`packaging` is not declared in `pyproject.toml`, so using it is a judgement call. It is
nonetheless a hard, direct dependency of pytest itself, so it is present in every environment
that can run this test at all — the test cannot be reached in an environment lacking it. The
alternative, hand-rolling PEP 440 specifier comparison, trades a phantom risk for real parsing
bugs in a test whose entire job is to be trustworthy. Use `packaging.specifiers.SpecifierSet`
and `packaging.version.Version`.

Parse the floor out of the specifier rather than string-matching `">=3.11"`, so that a
future `">=3.11,<4"` or `"~=3.11"` does not read as a regression.

## Risks

- **Installer refusal on 3.7–3.10.** Intended, and already effectively true — those
  interpreters cannot import `service_benchmark`. The change converts a confusing runtime
  `SyntaxError` into a clear resolver-level message.
- **A 3.12+ CI bump would fail the new guard** if `pythonVersion` is bumped and
  `requires-python` is not. That is the guard working as designed; the fix is to move both,
  which is one line each and now enforced.
- **Diff coverage.** No `src/` file changes, so the coverage gate reports no measurable lines.
  Expected — see proposal Impact. Do not manufacture a `src/` edit to satisfy it.
