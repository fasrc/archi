## Context

`compare_runs.py` runs as a plain script (`python scripts/benchmarking/compare_runs.py ...`)
or as `scripts.benchmarking.compare_runs` under pytest. As a script, `sys.path[0]` is
`scripts/benchmarking/`, so `scripts` and `src` are importable only after
`_project_root_on_path()` inserts the repository root. That is why `_category_modules()`
and `_lynx_report()` import their project modules inside the function, after the shim
(`compare_runs.py:421-433`), and why `_utils_module()` calls the shim first (`:1665`).

`src/utils/__init__.py` imports the config service and with it `psycopg2`. `_utils_module`
(`:1654-1679`) tries `importlib.import_module("src.utils.<module>")` and, on `ImportError`,
loads `src/utils/<module>.py` by path under the module name `archi_<module>`.
`benchmark_schema.py`, `benchmark_provenance.py` and `judge_gates.py` are pure stdlib at
import, so the file load works. `tests/unit/test_compare_runs.py:2495` and
`tests/unit/test_judge_gates.py:362` simulate the missing driver with
`monkeypatch.setitem(sys.modules, "src.utils", None)`: every package import of a
`src.utils` module then raises `ModuleNotFoundError`.

The three category helpers are imported only through the package (`scripts.benchmarking`
is a namespace package; there is no `__init__.py`), never as scripts, so a module-level
`from scripts.benchmarking.stdlib_utils import utils_function` is correct in them and
sorts with their existing `scripts.benchmarking` imports.

## Goals / Non-Goals

Goals:
- `_category_modules()` and each helper import on a host with no `psycopg2`.
- `compare_runs.py` keeps `_utils_module`, `_utils_function` and `_project_root_on_path`
  with the same behavior, and every existing test in the five benchmarking test files passes.
- With the driver present, each helper binds the same `canonical_source_url` function
  object as before.

Non-Goals:
- No change to `src/utils/__init__.py` (fasrc/archi#468 covers a lazy package).
- No copy of `canonical_source_url`.
- No change to the Lynx path (`_lynx_report`): `scripts/benchmarking/lynx/*.py` import no
  `src` module, so it already works without the driver.
- No docs page: the README's claim (`scripts/benchmarking/README.md:209`) is the one this
  change makes true again, and its text does not change.

## Decisions

D1. One shared loader module, `scripts/benchmarking/stdlib_utils.py`, with the body that
`_utils_module` had. It raises `ImportError` when the file cannot be loaded, and knows
nothing about `compare_runs.py` (no `CompareError`, no exit codes), so the helpers can import
it without a cycle. `compare_runs._utils_module` calls it and translates `ImportError` into
`CompareError(str(exc), EXIT_USAGE)`, the same exception and code as before. The file-load
failure that was `# pragma: no cover` in `compare_runs.py` is now covered in the shared
module by the missing-file test.

D2. Each helper binds `canonical_source_url` once, at module level, through
`utils_function("benchmark_provenance", "canonical_source_url")`, instead of the issue
plan's "small `_canonical_source_url()` function called at the use sites". The use sites are
per-row loops (`category_attribution.attribute`, `row_shares`, `category_slice._sources`,
`paired_tests._declared`). A per-call lookup goes through `importlib.import_module` on every
row, and on the host this change is for (no driver) the file-load fallback would execute
`benchmark_provenance.py` again on every call unless the loader cached it, and a cache
would need its own tests and would interact with the `sys.modules` simulation the tests
use. The module-level binding keeps the helpers' code and signatures unchanged, costs one
lookup per import, and satisfies every acceptance criterion of the issue. The binding is a
plain module attribute named `canonical_source_url`, so the tests can read it, and the
acceptance grep (`^from src.utils`) prints nothing.

D3. `_project_root_on_path` stays in `compare_runs.py` as the two-line bootstrap, and
`stdlib_utils.project_root_on_path` is the shared copy. `compare_runs.py` reaches
`stdlib_utils` through `from scripts.benchmarking import stdlib_utils` inside a new
`_stdlib_utils()` helper, which needs the repository root on `sys.path` first; a function in
`stdlib_utils` cannot do that for the module that imports it. The isolated-interpreter test
(design D5) calls `_utils_function` before anything else has run the shim, so a wrapper that
forgets the shim fails that test (mutant m6 below).

D4. The spec delta uses `ADDED`. The helpers belong to the capability
`category-slice-provenance`, which exists only in the unarchived change
`category-slice-in-compare-runs` (`openspec/changes/category-slice-in-compare-runs/specs/`),
so there is no `openspec/specs/category-slice-provenance/` to modify. Archive note: archive
`category-slice-in-compare-runs` first; this change's requirement then adds to that spec.

D5. One new test file, `tests/unit/test_stdlib_utils.py`, next to the per-module files, with
three fixtures: `stdlib_utils` imports the shim inside the fixture so an absent file fails
the test and not the collection; `no_driver` removes `src.utils.benchmark_provenance` and
`src.utils.benchmark_schema` from `sys.modules` and sets `src.utils` to `None`;
`fresh_helpers` removes the three helper modules from `sys.modules` AND deletes their
attributes on the `scripts.benchmarking` package, because `from scripts.benchmarking import
category_slice` returns the package attribute when it exists and would hand back the old
module. `monkeypatch` restores every entry and attribute, so the other test modules keep
their module objects. The last test runs `sys.executable -S -I -c <probe>` with
`cwd=tmp_path` and only `scripts/benchmarking/` on `sys.path`: the issue's repro as a plain
script run, with the loader called before `_category_modules()`.

D6. The branch is `fix/issue-816-stdlib-category-helpers` (the nightly convention that the
drain's deliverable check requires), not the body's `fix/compare-runs-stdlib-category-helpers`.
The claim comment on the issue says so.

## Measurement

Loop 1 measured these numbers on 2026-11-08 in scratch copies of `origin/dev` `54ce1eef`
(`git archive` of `scripts src tests examples pyproject.toml`), with the conda `archi`
interpreter (Python 3.11, black 24.10.0, isort 6.0.1). The patches are in tasks.md
Appendix A (task 1.1) and Appendix B (task 1.2); both apply to a fresh archive with
`git apply -p1` and the result is byte-identical to the staged copies.

`python -m pytest -q tests/unit/test_stdlib_utils.py`:

| Tree | Test file | Result |
|---|---|---|
| `origin/dev` | Appendix A's 7 tests | `1 passed, 6 errors` (every error `ModuleNotFoundError: No module named 'scripts.benchmarking.stdlib_utils'`; the pass is `test_compare_runs_turns_a_load_failure_into_a_usage_error`) |
| `origin/dev` + Appendix A | Appendix A's 7 tests | `7 passed` |
| `origin/dev` + Appendix A | all 15 tests | `5 failed, 10 passed` (the three `test_a_category_helper_imports_without_the_src_package[...]`, `test_category_modules_load_without_the_src_package`, `test_category_modules_load_in_an_isolated_interpreter_from_another_directory`) |
| `origin/dev` + A + B | all 15 tests | `15 passed` |
| `origin/dev` | all 15 tests | `5 failed, 4 passed, 6 errors` |

The five existing files (`test_compare_runs.py`, `test_compare_runs_category.py`,
`test_category_slice.py`, `test_category_attribution.py`, `test_paired_tests.py`):
`262 passed` on `origin/dev`, on `origin/dev` + A, and on `origin/dev` + A + B. The six
files together: `277 passed`, with the new file first or last. The whole unit suite on the
real checkout (`origin/dev` `54ce1eef` with A and B applied): `8148 passed, 60 skipped,
1 xfailed` in 2 min 36 s.

The issue's repro (`python -S -I`, repository root inserted on `sys.path`; tasks.md
Appendix C) on `origin/dev` + A + B prints `_category_modules(): OK`,
`category_slice: import OK`, `category_attribution: import OK`, `paired_tests: import OK`
and exits 0. On `origin/dev` it exits 1 with `ModuleNotFoundError: No module named 'psycopg2'`.

`grep -n "^from src.utils" scripts/benchmarking/category_slice.py scripts/benchmarking/category_attribution.py scripts/benchmarking/paired_tests.py`
prints nothing on `origin/dev` + A + B. `grep -n importlib scripts/benchmarking/compare_runs.py`
prints nothing on `origin/dev` + A.

Mutation check, one-line mutants of `origin/dev` + A + B, each run against the 15 tests:

| Mutant | Result | Named failures |
|---|---|---|
| m1 `utils_module` re-raises instead of the file fallback | `10 failed, 5 passed` | every no-driver test |
| m2 `compare_runs._utils_module` lets `ImportError` through | `1 failed, 14 passed` | `test_compare_runs_turns_a_load_failure_into_a_usage_error` |
| m3 `paired_tests` reverts to the package import | `3 failed, 12 passed` | `[paired_tests]`, `_category_modules` x2 |
| m4 `category_attribution` reverts | `4 failed, 11 passed` | `[category_attribution]`, `[category_slice]`, `_category_modules` x2 |
| m5 `category_slice` reverts | `3 failed, 12 passed` | `[category_slice]`, `_category_modules` x2 |
| m6 `_stdlib_utils()` forgets `_project_root_on_path()` | `1 failed, 14 passed` | `..._in_an_isolated_interpreter_from_another_directory` |
| m7 `project_root_on_path` inserts on every call | `1 failed, 14 passed` | `test_project_root_on_path_inserts_the_repository_root_once` |
| m8 a file-load failure is not wrapped as `ImportError` | `2 failed, 13 passed` | `..._raises_import_error_for_a_file_that_does_not_exist`, `..._usage_error` |

## Risks / Trade-offs

- A helper imported while the driver is absent keeps the file-loaded function for the life
  of the process, even if `src.utils` becomes importable later. The function is pure and
  identical, so no result changes; only the identity differs, and only in that case.
- The `python -S -I` subprocess test depends on `sys.executable` having a working stdlib
  without site-packages. The conda interpreter and the loop image's interpreter both do;
  the issue measured the same command.
- `scripts/` has no coverage gate (`diff-cover` reads `src/**` only), so a green gate says
  nothing about these lines. The counts above and the PR body carry the evidence.
