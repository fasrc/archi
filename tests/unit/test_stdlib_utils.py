"""The category helpers import on a host with no database driver (fasrc/archi#816).

``src/utils/__init__.py`` imports the config service, which imports ``psycopg2``.
``compare_runs.py`` documents a standard-library-only run for a host that only reads
finished artifacts, and loads the ``src/utils`` functions it needs through a shim
that falls back to the module's own file. The category helpers it imports on every
run (``category_slice``, ``category_attribution``, ``paired_tests``) imported
``canonical_source_url`` through the package, so ``_category_modules()`` failed on
that host. The shim now lives in ``scripts/benchmarking/stdlib_utils.py`` and the
helpers bind the function through it.

A missing driver is simulated the way ``test_compare_runs.py`` does: ``src.utils``
is set to ``None`` in ``sys.modules``, so every package import of a ``src.utils``
module raises ``ImportError``.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.benchmarking import compare_runs as cr
from src.utils.benchmark_provenance import canonical_source_url as package_function

KB = "https://docs.rc.fas.harvard.edu/kb"
HELPERS = ("category_attribution", "paired_tests", "category_slice")


@pytest.fixture
def stdlib_utils():
    """The shim, imported here so an absent file fails the test, not the collection."""
    return importlib.import_module("scripts.benchmarking.stdlib_utils")


@pytest.fixture
def no_driver(monkeypatch):
    """A host without psycopg2: every ``src.utils`` package import fails."""
    monkeypatch.delitem(sys.modules, "src.utils.benchmark_provenance", raising=False)
    monkeypatch.delitem(sys.modules, "src.utils.benchmark_schema", raising=False)
    monkeypatch.setitem(sys.modules, "src.utils", None)


@pytest.fixture
def fresh_helpers(monkeypatch):
    """Drop the helper modules so the next import runs their module code again.

    ``from scripts.benchmarking import category_slice`` reads the attribute on the
    package first, so the attribute goes too, not only the ``sys.modules`` entry.
    """
    package = importlib.import_module("scripts.benchmarking")
    for name in HELPERS:
        monkeypatch.delitem(sys.modules, f"scripts.benchmarking.{name}", raising=False)
        monkeypatch.delattr(package, name, raising=False)


# --- stdlib_utils ---------------------------------------------------------------


def test_utils_module_returns_the_package_module_when_the_driver_is_present(
    stdlib_utils,
):
    module = stdlib_utils.utils_module("benchmark_provenance")

    assert module is importlib.import_module("src.utils.benchmark_provenance")
    assert module.canonical_source_url is package_function


def test_utils_module_loads_the_file_without_the_src_package(stdlib_utils, no_driver):
    module = stdlib_utils.utils_module("benchmark_provenance")

    assert module.__name__ == "archi_benchmark_provenance"
    assert (
        Path(module.__file__)
        == stdlib_utils.REPO_ROOT / "src" / "utils" / "benchmark_provenance.py"
    )
    assert module.canonical_source_url is not package_function
    assert module.canonical_source_url(f"{KB}/running-jobs/") == package_function(
        f"{KB}/running-jobs/"
    )


def test_utils_function_returns_the_named_attribute(stdlib_utils, no_driver):
    function = stdlib_utils.utils_function(
        "benchmark_provenance", "canonical_source_url"
    )

    assert function.__module__ == "archi_benchmark_provenance"
    assert function(f"  {KB}/storage/  ") == f"{KB}/storage"


def test_utils_module_raises_import_error_for_a_file_that_does_not_exist(
    stdlib_utils, no_driver
):
    with pytest.raises(ImportError) as info:
        stdlib_utils.utils_module("no_such_module")

    assert "no_such_module.py" in str(info.value)


def test_project_root_on_path_inserts_the_repository_root_once(
    stdlib_utils, monkeypatch
):
    root = str(stdlib_utils.REPO_ROOT)
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry != root])

    stdlib_utils.project_root_on_path()
    stdlib_utils.project_root_on_path()

    assert sys.path[0] == root
    assert sys.path.count(root) == 1


# --- the compare_runs wrappers --------------------------------------------------


def test_compare_runs_loads_through_the_shared_shim(stdlib_utils, no_driver):
    module = cr._utils_module("benchmark_provenance")

    assert module.__name__ == "archi_benchmark_provenance"
    assert Path(module.__file__) == Path(
        stdlib_utils.utils_module("benchmark_provenance").__file__
    )
    function = cr._utils_function("benchmark_provenance", "canonical_source_url")
    assert function(f"{KB}/a/") == f"{KB}/a"


def test_compare_runs_turns_a_load_failure_into_a_usage_error(no_driver):
    with pytest.raises(cr.CompareError) as info:
        cr._utils_module("no_such_module")

    assert info.value.code == cr.EXIT_USAGE
    assert "no_such_module.py" in str(info.value)


# --- the category helpers -------------------------------------------------------


@pytest.mark.parametrize("name", HELPERS)
def test_a_category_helper_imports_without_the_src_package(
    name, no_driver, fresh_helpers
):
    module = importlib.import_module(f"scripts.benchmarking.{name}")

    assert module.canonical_source_url.__module__ == "archi_benchmark_provenance"
    assert module.canonical_source_url(f"{KB}/running-jobs/") == f"{KB}/running-jobs"


@pytest.mark.parametrize("name", HELPERS)
def test_a_category_helper_binds_the_package_function_when_the_driver_is_present(
    name, fresh_helpers
):
    module = importlib.import_module(f"scripts.benchmarking.{name}")

    assert module.canonical_source_url is package_function


def test_category_modules_load_without_the_src_package(no_driver, fresh_helpers):
    category_slice, paired_tests = cr._category_modules()

    assert (
        category_slice.canonical_source_url.__module__ == "archi_benchmark_provenance"
    )
    assert category_slice.canonical_source_url(f"{KB}/x/") == f"{KB}/x"
    assert paired_tests.canonical_source_url(f"{KB}/x/") == f"{KB}/x"


def test_category_modules_load_in_an_isolated_interpreter_from_another_directory(
    tmp_path,
):
    # The issue's repro as a plain script run: no site-packages (-S, so no psycopg2),
    # no cwd or PYTHONPATH on sys.path (-I), a cwd outside the repository, and only
    # the script's own directory on sys.path, as `python scripts/benchmarking/...`
    # has it. Only the script's own shim can make `scripts` and `src` importable.
    script_dir = Path(cr.__file__).resolve().parent
    # `_utils_function` goes first: at that point nothing but its own shim has put
    # the repository root on sys.path, so a shim that forgets to is caught here.
    probe = (
        "import sys\n"
        f"sys.path.insert(0, {str(script_dir)!r})\n"
        "import compare_runs\n"
        "loaded = compare_runs._utils_function("
        "'benchmark_provenance', 'canonical_source_url')\n"
        "print(loaded.__module__)\n"
        "category_slice, paired_tests = compare_runs._category_modules()\n"
        "print(category_slice.canonical_source_url.__module__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-I", "-c", probe],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        "archi_benchmark_provenance",
        "archi_benchmark_provenance",
    ]
