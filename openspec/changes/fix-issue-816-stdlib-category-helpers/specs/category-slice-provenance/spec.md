## ADDED Requirements

### Requirement: The category helpers SHALL import on a host with no database driver
`scripts/benchmarking/category_slice.py`, `category_attribution.py` and `paired_tests.py` SHALL bind `canonical_source_url` through the pure-stdlib loader `scripts/benchmarking/stdlib_utils.py`, never through a module-level `from src.utils...` import, so that `compare_runs.py`'s `_category_modules()` and an ordinary comparison run on a host that has no `psycopg2`.
The loader SHALL try the package import first and otherwise load `src/utils/<module>.py`
from disk under the module name `archi_<module>` (same file, same function, never a copy),
and SHALL raise `ImportError` when the file cannot be loaded either. `compare_runs.py`
SHALL keep `_utils_module`, `_utils_function` and `_project_root_on_path` with their
current names and behavior as wrappers over the loader, and `_utils_module` SHALL turn the
loader's `ImportError` into `CompareError` with code `EXIT_USAGE`.

#### Scenario: an isolated interpreter runs the comparison's category helpers
- **WHEN** `python -S -I` (no site-packages, so no `psycopg2`) imports
  `scripts.benchmarking.compare_runs` with the repository root on `sys.path`, calls
  `_category_modules()`, and then imports `scripts.benchmarking.category_slice`,
  `category_attribution` and `paired_tests`
- **THEN** every step succeeds and the process exits 0, and each helper's
  `canonical_source_url` is the function loaded from `src/utils/benchmark_provenance.py`
  (its `__module__` is `archi_benchmark_provenance`)

#### Scenario: a plain script run from another directory
- **WHEN** `compare_runs` is imported in `python -S -I` with only `scripts/benchmarking/`
  on `sys.path` and a current directory outside the repository, and
  `_utils_function("benchmark_provenance", "canonical_source_url")` is called before
  `_category_modules()`
- **THEN** both calls succeed through the script's own `_project_root_on_path` shim

#### Scenario: the driver is present
- **WHEN** `src.utils` imports normally and a helper module is imported
- **THEN** the helper's `canonical_source_url` is the package function object itself, so
  no existing result changes

#### Scenario: a file that cannot be loaded is a usage error
- **WHEN** `compare_runs._utils_module("no_such_module")` runs with `src.utils` unavailable
- **THEN** it raises `CompareError` whose `code` is `EXIT_USAGE` (1) and whose message names
  `no_such_module.py`

#### Scenario: no helper imports through the package
- **WHEN** `grep -n "^from src.utils" scripts/benchmarking/category_slice.py scripts/benchmarking/category_attribution.py scripts/benchmarking/paired_tests.py` runs
- **THEN** it prints nothing
