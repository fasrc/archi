## Why

`src/cli/managers/secrets_manager.py` is not black-clean. Measured on `origin/dev`
(`07e007df`), `black --check --diff` on that one file emits **81** changed lines in a
185-line module: blank lines around the class, a wrapped `logger.warning(...)`, a wrapped
`get_secrets` signature, a split boolean expression, and trailing whitespace.

The project gate runs black in two modes, and the local one is a **formatter, not a checker**:
`_format_changed` (`scripts/gate.sh:74-80`) rewrites the changed files in place before the gate
scores patch coverage with `diff-cover --fail-under=80` against `origin/dev`. (In CI,
`_check_format_scope` at `scripts/gate.sh:65-71` runs `black --check` and rewrites nothing — see
the spec delta for why that distinction matters here.) So the moment anyone edits one line of
this module locally, black reflows the rest of it, `diff-cover` counts every reflowed line as
part of the patch, and the gate fails for reasons unrelated to the edit.

Issue #291 records where this bit: in #287, the natural fix was to improve the operator-facing
error in `validate_secrets` (`src/cli/managers/secrets_manager.py:123-141` on `07e007df`;
`:144-162` after this change's reflow), which names
`src/cli/managers/secrets_dummy.env` — a placeholder inside archi's own package — as the file
to add secrets to. The change was routed into `src/cli/cli_main.py` instead, partly on layering
grounds and partly because editing this module would have failed the gate. The layering
argument stands alone; the gate constraint should not have been a factor, and it will keep
distorting decisions until the module is reformatted.

## What Changes

- Reformat `src/cli/managers/secrets_manager.py` with black 24.10.0 and isort 6.0.1. No
  behaviour change, no renames, no signature changes, no docstring rewrites.
- Add the first unit tests to reach four members of `SecretsManager`, because **the reformat
  alone cannot pass the gate**. The patch touches 51 lines, and `diff-cover` finds 15
  measurable statements among them.

  Planning estimated 16 measurable lines and 8 uncovered, for 50%. The measured figures, taken
  on the reformatted module with the characterization tests removed, are **15 measurable and 7
  uncovered — patch coverage 53.3%, against a floor of 80%**, with the uncovered lines at
  87, 90, 95, 100 (`_get_model_based_secrets`), 180, 184 (`write_secrets_to_files`), and 195
  (`write_env_file`). `get_env_file_path` was estimated uncovered and is not. Covering the seven
  lifts the patch to 15/15. Both sets of numbers are recorded because the estimate is what the
  plan was built on and the measurement is what the gate actually scores; where they differ, the
  measurement governs.
- Prove the reformat is behaviour-preserving by **AST equality**, not by `git diff -w`. The
  issue proposes `git diff -w` as the proof; that check does not hold, because `-w` ignores
  whitespace *within* a line and black's reflow changes line *boundaries*. Run on the real
  reformat, `git diff -w` still prints the wrapped `logger.warning`, the wrapped `get_secrets`
  signature, and the split `required = ... | ...` expression. Comparing
  `ast.dump(ast.parse(src))` before and after is exact and cannot be fooled by wrapping.

**Out of scope, deliberately:** the `validate_secrets` message that motivated #287, and the
`src/cli/managers/secrets_dummy.env` default. Both are behaviour. This change exists to make
editing them cheap later; doing it here would defeat the point of a separable formatting
commit.

## Capabilities

### New Capabilities
- `secret-provisioning`: the contract of `SecretsManager` — how it derives which secrets a
  deployment requires, how it writes them out for compose, and the formatting hygiene the
  module must keep so that behavioural edits to it are affordable. Not currently present in
  `openspec/specs/`, so the delta is `ADDED`.

### Modified Capabilities
<!-- None. No capability already in openspec/specs/ changes. -->

## Impact

**Code**
- `src/cli/managers/secrets_manager.py` — formatting only, whole file. 81 lines of black
  churn, 51 lines in the resulting patch.

**Tests**
- `tests/unit/test_secret_manager_provisioning.py` (new) — characterization tests for
  `_get_model_based_secrets`, `write_secrets_to_files`, `write_env_file`, and
  `get_env_file_path`. These pin behaviour that exists today; they are written and made green
  **before** the reformat, so that the reformat has to keep them green.

**Acceptance criterion amended.** Issue #291 asks that the PR touch exactly one file. That is
not achievable: a one-file formatting patch scores 50% and the gate refuses it at 80%. The PR
touches **two** — one reformatted source file and one new test file. Adding tests is not a
behaviour change, so the issue's real constraint ("formatting only") is honoured; only its
file count moves. This is the one place this change knowingly departs from the issue text, and
it must be called out in the PR body.

**Not deployment-affecting.** No config, template, or compose change; nothing to redeploy.

## Risks

- **A "formatting" change that is not.** Mitigated by the AST-equality proof, which is exact,
  plus the full unit suite.
- **Coverage that lands below the floor anyway.** Mitigated by measuring patch coverage
  explicitly in the task list rather than inferring it from a green gate.
- **The drift recurs.** The `*secrets*` pattern at `.gitignore:19` matches this tracked source
  file's basename, so black's *directory* walk — which is what the CI whole-scope format assert
  uses — skips it, while the local pre-commit writer, which names changed paths explicitly,
  reflows it. That is why CI is green on `dev` while the file is misformatted, and it means
  reformatting clears today's churn without preventing the next drift. Narrowing that pattern
  would weaken a secret-leak guard, so it is **out of scope and flagged for a human** rather
  than changed here (design.md, Decision 6).
- **A stale root `tasks.md` symlink reddens the suite.** `tests/unit/test_python_version_declaration.py::test_every_page_stating_a_minimum_is_guarded`
  globs markdown at the repo root and follows that symlink; when it dangles, the test fails
  with `FileNotFoundError` and looks like a regression in this change. The file is gitignored.
  Repoint it, do not "fix" the test.
