## Why

`scripts/benchmarking/compare_runs.py` documents a standard-library-only run for a host
that only reads finished benchmark artifacts ("no deployment, no database",
`scripts/benchmarking/README.md:209`). On `origin/dev` `54ce1eef` that run fails on a host
with no `psycopg2` (fasrc/archi#816):

- `run()` (`compare_runs.py:3228`) calls `category_map_rules(...)` on every run (`:3278`),
  which calls `_category_modules()` (`:421-426`). That function imports
  `scripts.benchmarking.category_slice` and `paired_tests` through the package.
- `category_slice.py:38`, `category_attribution.py:32` (reached from `category_slice.py:32`)
  and `paired_tests.py:22` each do `from src.utils.benchmark_provenance import
  canonical_source_url` at module level. That import runs `src/utils/__init__.py:8`, which
  imports `src/utils/config_service.py`, whose line 16 imports `psycopg2`.
- `compare_runs.py` already has a loader for exactly this case: `_utils_module()`
  (`:1654-1679`) tries the package import and otherwise loads `src/utils/<module>.py` from
  disk. `retrieval_identity` (`:732`), `judge_gates` (`:1026`) and `normalize_bank` (`:1684`)
  go through it. The category helpers do not.

Measured on 2026-11-08 in a `git archive` copy of `origin/dev` `54ce1eef` with
`python -S -I` (no site-packages, so no `psycopg2`) and the repository root on `sys.path`:
`compare_runs` imports, `_utils_function("benchmark_provenance", "canonical_source_url")`
works, and `_category_modules()` plus each of the three helper imports raise
`ModuleNotFoundError: No module named 'psycopg2'`. The issue's repro script exits 1.

Origin: Codex P2 finding on the release PR #762
(https://github.com/fasrc/archi/pull/762#discussion_r4365990093). The release shipped with
it; this is the follow-up.

## What Changes

- A new pure-stdlib module `scripts/benchmarking/stdlib_utils.py` holds the loader that
  `_utils_module` was: `REPO_ROOT`, `project_root_on_path()`, `utils_module(name)` and
  `utils_function(name, attr)`. A file that cannot be loaded raises `ImportError` there.
- `compare_runs.py` keeps `_project_root_on_path`, `_utils_module` and `_utils_function`
  with the same names and behavior. `_utils_module` is now a thin wrapper that calls
  `stdlib_utils.utils_module` and turns its `ImportError` into `CompareError(EXIT_USAGE)`,
  as before. The now-unused `import importlib.util` goes. `_project_root_on_path` stays
  in `compare_runs.py` because the shared module is itself reached through it (design D3).
- `category_slice.py`, `category_attribution.py` and `paired_tests.py` replace the
  module-level `from src.utils.benchmark_provenance import canonical_source_url` with one
  module-level binding, `canonical_source_url = utils_function("benchmark_provenance",
  "canonical_source_url")`, through the new module (design D2). Every public signature and
  every use site is unchanged; the function is loaded from the same file, never copied.
- A new `tests/unit/test_stdlib_utils.py` (15 tests) covers the loader, the
  `compare_runs.py` wrappers, each helper's import with `src.utils` unavailable, the
  package-function identity when the driver is present, `_category_modules()` without the
  driver, and a `python -S -I` subprocess run from a directory outside the repository.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `category-slice-provenance` (added by the unarchived change
  `category-slice-in-compare-runs`, which added the three helper modules): the helpers
  import on a host with no database driver. The delta uses `ADDED` because the capability
  is not yet in `openspec/specs/` (design D4).

## Impact

- `scripts/benchmarking/compare_runs.py`, `category_slice.py`, `category_attribution.py`,
  `paired_tests.py`; new `scripts/benchmarking/stdlib_utils.py`; new
  `tests/unit/test_stdlib_utils.py`. No change to `src/utils/__init__.py` (out of scope,
  see fasrc/archi#468), no docs page (the README's "no deployment, no database" sentence
  becomes true again; it does not change), no `deploy/**`.
- The PR also adds this plan folder. The nightly loop reads its task list from here, the
  same as PR #811 and PR #815.
- With `psycopg2` present nothing changes: the package import is tried first, and each
  helper binds the very same function object as before (the tests assert the identity).
- `diff-cover` scores `src/**/*.py` lines only, so this diff scores no lines and the gate's
  coverage step cannot vouch for it. The named tests and their counts are the evidence.
