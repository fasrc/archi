## 1. Load the category helpers' one `src.utils` function through a shared pure-stdlib shim (fasrc/archi#816)

- [x] 1.1 Red then green in one task (do not commit a red state). Write Appendix A to
      `/tmp/816-A.diff` with the Write tool; do not rewrite the patch, copy it. From the
      repository root, apply its test half first:
      `git apply -p1 --include='tests/*' /tmp/816-A.diff`. Run
      `python -m pytest -q tests/unit/test_stdlib_utils.py` and confirm EXACTLY
      `1 passed, 6 errors` (every error is `ModuleNotFoundError: No module named
      'scripts.benchmarking.stdlib_utils'`; the pass is
      `test_compare_runs_turns_a_load_failure_into_a_usage_error`). Then apply the rest:
      `git apply -p1 --exclude='tests/*' /tmp/816-A.diff`. It adds
      `scripts/benchmarking/stdlib_utils.py` (`REPO_ROOT`, `project_root_on_path`,
      `utils_module`, `utils_function`: the loader that `_utils_module` was) and makes
      `_utils_module` and `_utils_function` in `scripts/benchmarking/compare_runs.py` thin
      wrappers over it (design D1, D3). Confirm EXACTLY `7 passed` from the same pytest
      command. Run
      `python -m pytest -q tests/unit/test_compare_runs.py tests/unit/test_compare_runs_category.py tests/unit/test_category_slice.py tests/unit/test_category_attribution.py tests/unit/test_paired_tests.py`
      and confirm `262 passed`. Confirm that `grep -n importlib scripts/benchmarking/compare_runs.py`
      prints nothing. Confirm that `git status --porcelain` lists only the three files of the
      patch (and this `tasks.md`). If a count or a line differs, STOP and write what you saw
      under "## Measurement" in `design.md`. Tick this box. Run `bash scripts/gate.sh` in the
      foreground (never in the background) and wait for exit 0. Commit the patch and the tick
      together only when the gate is green (message:
      `refactor(benchmarking): share compare_runs' pure-stdlib src.utils loader`).

- [x] 1.2 Red then green in one task (do not commit a red state). Write Appendix B to
      `/tmp/816-B.diff` with the Write tool; copy it exactly. Apply its test half first:
      `git apply -p1 --include='tests/*' /tmp/816-B.diff`. Run
      `python -m pytest -q tests/unit/test_stdlib_utils.py` and confirm EXACTLY
      `5 failed, 10 passed` (the failures are the three
      `test_a_category_helper_imports_without_the_src_package[...]` cases,
      `test_category_modules_load_without_the_src_package` and
      `test_category_modules_load_in_an_isolated_interpreter_from_another_directory`; each
      ends in a `ModuleNotFoundError` for `src.utils` or `psycopg2`). Then apply the rest:
      `git apply -p1 --exclude='tests/*' /tmp/816-B.diff`. It replaces the module-level
      `from src.utils.benchmark_provenance import canonical_source_url` in
      `scripts/benchmarking/category_slice.py`, `category_attribution.py` and
      `paired_tests.py` with one binding through `stdlib_utils.utils_function` (design D2).
      Confirm EXACTLY `15 passed` from the same pytest command. Confirm that
      `grep -n "^from src.utils" scripts/benchmarking/category_slice.py scripts/benchmarking/category_attribution.py scripts/benchmarking/paired_tests.py`
      prints nothing. Write Appendix C to `/tmp/816-repro.py` with the Write tool and run
      `python -S -I /tmp/816-repro.py` from the repository root; confirm it prints the four
      `OK` lines listed there and exits 0. Run
      `python -m pytest -q tests/unit/test_stdlib_utils.py tests/unit/test_compare_runs.py tests/unit/test_compare_runs_category.py tests/unit/test_category_slice.py tests/unit/test_category_attribution.py tests/unit/test_paired_tests.py`
      and confirm `277 passed`. Confirm that `git status --porcelain` lists only the four
      files of the patch (and this `tasks.md`). If a count or a line differs, STOP and write
      what you saw under "## Measurement" in `design.md`. Tick this box. Run
      `bash scripts/gate.sh` in the foreground (never in the background) and wait for exit 0.
      Commit the patch and the tick together only when the gate is green (message:
      `fix(benchmarking): import the category helpers without src.utils (#816)`).

## 2. Publish

- [x] 2.1 Tick this box, commit the tick through the hook (message: `mark #816 tasks complete`),
      then push and open the PR, so the push carries every commit. Push with
      `git push -u origin fix/issue-816-stdlib-category-helpers`. Open the PR with
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-816-stdlib-category-helpers`,
      the title `fix(benchmarking): import the category helpers without src.utils (#816)`,
      and `Closes #816` in the BODY (not the title). Pass the body with `--body` or a file
      under `/tmp`; do NOT commit a PR-body file to the repository. In the body: copy the
      "## Measurement" tables of `design.md` (the red and green counts are the acceptance
      evidence "new tests that fail on `origin/dev` and pass on the branch"; name the five
      failing tests and the six errors); say that the `python -S -I` repro prints `OK` on
      every line and exits 0; say that `grep -n "^from src.utils"` over the three helpers
      prints nothing; say that `_utils_function`, `_utils_module` and `_project_root_on_path`
      keep their names and behavior; say that each helper binds the function once at module
      level through the shim (design D2) instead of the issue plan's per-call lookup, and
      why; say that `diff-cover` scores `src/**` only, so the gate's coverage step shows no
      lines for this diff and the named tests are the evidence; say that the PR also adds
      this plan folder (`openspec/changes/fix-issue-816-stdlib-category-helpers/`), the same
      as PR #815, so `git diff --name-only origin/dev...HEAD` lists it besides the six code
      files; give the archive note from design D4 in one sentence; and name the origin
      thread https://github.com/fasrc/archi/pull/762#discussion_r4365990093. Then link the
      PR from that thread, one try:
      `gh api -X POST repos/fasrc/archi/pulls/762/comments/4365990093/replies -f body="Follow-up: fasrc/archi#816, fixed in <PR URL>."`
      (if the call fails, write the failure in your turn note and continue; the wrap-up
      posts it). Confirm
      `git ls-remote origin refs/heads/fix/issue-816-stdlib-category-helpers` prints the
      same SHA as `git rev-parse HEAD`. STOP at the open PR - do not merge.

## Appendix (Loop 1 scratch evidence, 2026-11-08)

Loop 1 ran every command above in scratch copies of `origin/dev` `54ce1eef` (`git archive`
of `scripts src tests examples pyproject.toml`) and recorded the output in `design.md`
"## Measurement". Both patches apply to a fresh archive with `git apply --check -p1`, in
order, and the result is byte-identical to the staged files; the `--include='tests/*'` then
`--exclude='tests/*'` split gives the same result. Copy the text exactly. These appendix
sections have no checkboxes.

### Appendix A (task 1.1: `stdlib_utils.py`, the `compare_runs.py` wrappers, 7 tests)

````diff
diff --git a/scripts/benchmarking/compare_runs.py b/scripts/benchmarking/compare_runs.py
index dee34cf..c9c4d30 100644
--- a/scripts/benchmarking/compare_runs.py
+++ b/scripts/benchmarking/compare_runs.py
@@ -79,7 +79,6 @@ Exit codes: 0 ok, 1 usage/IO, 2 gate refusal, 3 config-divergence stop.
 from __future__ import annotations
 
 import argparse
-import importlib.util
 import json
 import math
 import re
@@ -1636,16 +1635,26 @@ def timing_block(arms: Sequence[Arm]) -> List[dict]:
 
 
 def _project_root_on_path() -> None:
-    """Make ``src`` importable from a plain ``python scripts/...`` run.
+    """Make ``src`` and ``scripts`` importable from a plain ``python scripts/...`` run.
 
-    The two project imports below happen *inside* their functions on purpose:
+    The project imports below happen *inside* their functions on purpose:
     a module-level import would be sorted above this shim by isort and the
     script would then only work when it was already run from the repo root.
+    ``stdlib_utils`` owns the shared copy of this shim, but it is itself reached
+    through this one, so the two lines stay here as well.
     """
     if str(REPO_ROOT) not in sys.path:
         sys.path.insert(0, str(REPO_ROOT))
 
 
+def _stdlib_utils() -> Any:
+    """The pure-stdlib ``src/utils`` loader shared with the category helpers (#816)."""
+    _project_root_on_path()
+    from scripts.benchmarking import stdlib_utils
+
+    return stdlib_utils
+
+
 def _utils_function(module_name: str, name: str) -> Any:
     """A function from a pure-stdlib ``src/utils`` file (see ``_utils_module``)."""
     return getattr(_utils_module(module_name), name)
@@ -1655,28 +1664,16 @@ def _utils_module(module_name: str) -> Any:
     """A pure-stdlib ``src/utils`` module, without the package side effects.
 
     ``src/utils/__init__.py`` imports the config service, which imports
-    ``psycopg2`` — so the ordinary ``from src.utils.<module> import ...``
+    ``psycopg2``, so the ordinary ``from src.utils.<module> import ...``
     needs a database driver on a host that is only reading finished artifacts.
-    ``benchmark_schema.py``, ``benchmark_provenance.py`` and ``judge_gates.py``
-    are themselves pure stdlib at import, so the file is loaded straight from
-    disk when the package import is unavailable. Same file, same function: this
-    is not a copy.
+    ``stdlib_utils.utils_module`` tries the package import first and otherwise
+    loads the module's own file straight from disk. Same file, same function:
+    this is not a copy. A file that cannot be loaded either is a usage error.
     """
-    _project_root_on_path()
     try:
-        return importlib.import_module(f"src.utils.{module_name}")
-    except ImportError:
-        pass
-    source = REPO_ROOT / "src" / "utils" / f"{module_name}.py"
-    spec = importlib.util.spec_from_file_location(f"archi_{module_name}", source)
-    if spec is None or spec.loader is None:  # pragma: no cover - unreachable
-        raise CompareError(f"cannot load {source}", EXIT_USAGE)
-    module = importlib.util.module_from_spec(spec)
-    try:
-        spec.loader.exec_module(module)
-    except Exception as exc:  # pragma: no cover - environment, not logic
-        raise CompareError(f"cannot load {source}: {exc}", EXIT_USAGE) from None
-    return module
+        return _stdlib_utils().utils_module(module_name)
+    except ImportError as exc:
+        raise CompareError(str(exc), EXIT_USAGE) from None
 
 
 def _normalize_bank():
diff --git a/scripts/benchmarking/stdlib_utils.py b/scripts/benchmarking/stdlib_utils.py
new file mode 100644
index 0000000..863654a
--- /dev/null
+++ b/scripts/benchmarking/stdlib_utils.py
@@ -0,0 +1,62 @@
+"""Load a pure-stdlib ``src/utils`` module without the package side effects.
+
+``src/utils/__init__.py`` imports the config service, which imports ``psycopg2``,
+so the ordinary ``from src.utils.<module> import ...`` needs a database driver on a
+host that is only reading finished benchmark artifacts. ``compare_runs.py`` and the
+category helpers it imports on every run (``category_slice.py``,
+``category_attribution.py``, ``paired_tests.py``) share this loader instead
+(fasrc/archi#816): the package import is tried first, and when it is unavailable
+the module's own file is loaded straight from disk. Same file, same function: this
+is not a copy.
+
+Only ``src/utils`` modules that are themselves pure stdlib at import can be loaded
+this way; today those are ``benchmark_schema.py``, ``benchmark_provenance.py`` and
+``judge_gates.py``. This module is standard library only, on purpose: it must
+import on a host with no project dependencies at all.
+"""
+
+from __future__ import annotations
+
+import importlib
+import importlib.util
+import sys
+from pathlib import Path
+from types import ModuleType
+from typing import Any
+
+#: The repository root: ``scripts/benchmarking/stdlib_utils.py`` is two levels down.
+REPO_ROOT = Path(__file__).resolve().parents[2]
+
+
+def project_root_on_path() -> None:
+    """Make ``src`` and ``scripts`` importable from a plain ``python scripts/...`` run."""
+    if str(REPO_ROOT) not in sys.path:
+        sys.path.insert(0, str(REPO_ROOT))
+
+
+def utils_module(module_name: str) -> ModuleType:
+    """``src.utils.<module_name>``: the package module when it imports, else its file.
+
+    Raises ``ImportError`` when the file cannot be loaded either. The caller decides
+    how to report that; ``compare_runs.py`` turns it into a usage exit.
+    """
+    project_root_on_path()
+    try:
+        return importlib.import_module(f"src.utils.{module_name}")
+    except ImportError:
+        pass
+    source = REPO_ROOT / "src" / "utils" / f"{module_name}.py"
+    spec = importlib.util.spec_from_file_location(f"archi_{module_name}", source)
+    if spec is None or spec.loader is None:  # pragma: no cover - unreachable
+        raise ImportError(f"cannot load {source}")
+    module = importlib.util.module_from_spec(spec)
+    try:
+        spec.loader.exec_module(module)
+    except Exception as exc:
+        raise ImportError(f"cannot load {source}: {exc}") from None
+    return module
+
+
+def utils_function(module_name: str, name: str) -> Any:
+    """A function from a pure-stdlib ``src/utils`` file (see ``utils_module``)."""
+    return getattr(utils_module(module_name), name)
diff --git a/tests/unit/test_stdlib_utils.py b/tests/unit/test_stdlib_utils.py
new file mode 100644
index 0000000..12c9736
--- /dev/null
+++ b/tests/unit/test_stdlib_utils.py
@@ -0,0 +1,135 @@
+"""The category helpers import on a host with no database driver (fasrc/archi#816).
+
+``src/utils/__init__.py`` imports the config service, which imports ``psycopg2``.
+``compare_runs.py`` documents a standard-library-only run for a host that only reads
+finished artifacts, and loads the ``src/utils`` functions it needs through a shim
+that falls back to the module's own file. The category helpers it imports on every
+run (``category_slice``, ``category_attribution``, ``paired_tests``) imported
+``canonical_source_url`` through the package, so ``_category_modules()`` failed on
+that host. The shim now lives in ``scripts/benchmarking/stdlib_utils.py`` and the
+helpers bind the function through it.
+
+A missing driver is simulated the way ``test_compare_runs.py`` does: ``src.utils``
+is set to ``None`` in ``sys.modules``, so every package import of a ``src.utils``
+module raises ``ImportError``.
+"""
+
+from __future__ import annotations
+
+import importlib
+import sys
+from pathlib import Path
+
+import pytest
+
+from scripts.benchmarking import compare_runs as cr
+from src.utils.benchmark_provenance import canonical_source_url as package_function
+
+KB = "https://docs.rc.fas.harvard.edu/kb"
+HELPERS = ("category_attribution", "paired_tests", "category_slice")
+
+
+@pytest.fixture
+def stdlib_utils():
+    """The shim, imported here so an absent file fails the test, not the collection."""
+    return importlib.import_module("scripts.benchmarking.stdlib_utils")
+
+
+@pytest.fixture
+def no_driver(monkeypatch):
+    """A host without psycopg2: every ``src.utils`` package import fails."""
+    monkeypatch.delitem(sys.modules, "src.utils.benchmark_provenance", raising=False)
+    monkeypatch.delitem(sys.modules, "src.utils.benchmark_schema", raising=False)
+    monkeypatch.setitem(sys.modules, "src.utils", None)
+
+
+@pytest.fixture
+def fresh_helpers(monkeypatch):
+    """Drop the helper modules so the next import runs their module code again.
+
+    ``from scripts.benchmarking import category_slice`` reads the attribute on the
+    package first, so the attribute goes too, not only the ``sys.modules`` entry.
+    """
+    package = importlib.import_module("scripts.benchmarking")
+    for name in HELPERS:
+        monkeypatch.delitem(sys.modules, f"scripts.benchmarking.{name}", raising=False)
+        monkeypatch.delattr(package, name, raising=False)
+
+
+# --- stdlib_utils ---------------------------------------------------------------
+
+
+def test_utils_module_returns_the_package_module_when_the_driver_is_present(
+    stdlib_utils,
+):
+    module = stdlib_utils.utils_module("benchmark_provenance")
+
+    assert module is importlib.import_module("src.utils.benchmark_provenance")
+    assert module.canonical_source_url is package_function
+
+
+def test_utils_module_loads_the_file_without_the_src_package(stdlib_utils, no_driver):
+    module = stdlib_utils.utils_module("benchmark_provenance")
+
+    assert module.__name__ == "archi_benchmark_provenance"
+    assert (
+        Path(module.__file__)
+        == stdlib_utils.REPO_ROOT / "src" / "utils" / "benchmark_provenance.py"
+    )
+    assert module.canonical_source_url is not package_function
+    assert module.canonical_source_url(f"{KB}/running-jobs/") == package_function(
+        f"{KB}/running-jobs/"
+    )
+
+
+def test_utils_function_returns_the_named_attribute(stdlib_utils, no_driver):
+    function = stdlib_utils.utils_function(
+        "benchmark_provenance", "canonical_source_url"
+    )
+
+    assert function.__module__ == "archi_benchmark_provenance"
+    assert function(f"  {KB}/storage/  ") == f"{KB}/storage"
+
+
+def test_utils_module_raises_import_error_for_a_file_that_does_not_exist(
+    stdlib_utils, no_driver
+):
+    with pytest.raises(ImportError) as info:
+        stdlib_utils.utils_module("no_such_module")
+
+    assert "no_such_module.py" in str(info.value)
+
+
+def test_project_root_on_path_inserts_the_repository_root_once(
+    stdlib_utils, monkeypatch
+):
+    root = str(stdlib_utils.REPO_ROOT)
+    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry != root])
+
+    stdlib_utils.project_root_on_path()
+    stdlib_utils.project_root_on_path()
+
+    assert sys.path[0] == root
+    assert sys.path.count(root) == 1
+
+
+# --- the compare_runs wrappers --------------------------------------------------
+
+
+def test_compare_runs_loads_through_the_shared_shim(stdlib_utils, no_driver):
+    module = cr._utils_module("benchmark_provenance")
+
+    assert module.__name__ == "archi_benchmark_provenance"
+    assert Path(module.__file__) == Path(
+        stdlib_utils.utils_module("benchmark_provenance").__file__
+    )
+    function = cr._utils_function("benchmark_provenance", "canonical_source_url")
+    assert function(f"{KB}/a/") == f"{KB}/a"
+
+
+def test_compare_runs_turns_a_load_failure_into_a_usage_error(no_driver):
+    with pytest.raises(cr.CompareError) as info:
+        cr._utils_module("no_such_module")
+
+    assert info.value.code == cr.EXIT_USAGE
+    assert "no_such_module.py" in str(info.value)
````

### Appendix B (task 1.2: the three helpers, 8 more tests)

````diff
diff --git a/scripts/benchmarking/category_attribution.py b/scripts/benchmarking/category_attribution.py
index ba34c71..1cb0971 100644
--- a/scripts/benchmarking/category_attribution.py
+++ b/scripts/benchmarking/category_attribution.py
@@ -29,7 +29,13 @@ from __future__ import annotations
 from dataclasses import dataclass, field
 from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
 
-from src.utils.benchmark_provenance import canonical_source_url
+from scripts.benchmarking.stdlib_utils import utils_function
+
+#: ``src.utils.benchmark_provenance.canonical_source_url``, bound through the
+#: pure-stdlib shim so this module imports on a host with no database driver
+#: (``src/utils/__init__.py`` imports psycopg2; fasrc/archi#816). Same file,
+#: same function: this is not a copy.
+canonical_source_url = utils_function("benchmark_provenance", "canonical_source_url")
 
 #: A URL the map assigns to two different categories. It is reported as
 #: unresolved rather than silently given one of them.
diff --git a/scripts/benchmarking/category_slice.py b/scripts/benchmarking/category_slice.py
index c7f3c24..3d78383 100644
--- a/scripts/benchmarking/category_slice.py
+++ b/scripts/benchmarking/category_slice.py
@@ -35,7 +35,13 @@ from scripts.benchmarking.category_attribution import (
     metric_rows,
     url_categories,
 )
-from src.utils.benchmark_provenance import canonical_source_url
+from scripts.benchmarking.stdlib_utils import utils_function
+
+#: ``src.utils.benchmark_provenance.canonical_source_url``, bound through the
+#: pure-stdlib shim so this module imports on a host with no database driver
+#: (``src/utils/__init__.py`` imports psycopg2; fasrc/archi#816). Same file,
+#: same function: this is not a copy.
+canonical_source_url = utils_function("benchmark_provenance", "canonical_source_url")
 
 UNAVAILABLE = "<unavailable:"
 METADATA_TOOL = "search_metadata_index"
diff --git a/scripts/benchmarking/paired_tests.py b/scripts/benchmarking/paired_tests.py
index 6f9eaa5..d86d969 100644
--- a/scripts/benchmarking/paired_tests.py
+++ b/scripts/benchmarking/paired_tests.py
@@ -19,7 +19,13 @@ from __future__ import annotations
 import math
 from typing import Any, Dict, List, Mapping, Sequence
 
-from src.utils.benchmark_provenance import canonical_source_url
+from scripts.benchmarking.stdlib_utils import utils_function
+
+#: ``src.utils.benchmark_provenance.canonical_source_url``, bound through the
+#: pure-stdlib shim so this module imports on a host with no database driver
+#: (``src/utils/__init__.py`` imports psycopg2; fasrc/archi#816). Same file,
+#: same function: this is not a copy.
+canonical_source_url = utils_function("benchmark_provenance", "canonical_source_url")
 
 
 def mcnemar_exact(b: int, c: int) -> float:
diff --git a/tests/unit/test_stdlib_utils.py b/tests/unit/test_stdlib_utils.py
index 12c9736..c104251 100644
--- a/tests/unit/test_stdlib_utils.py
+++ b/tests/unit/test_stdlib_utils.py
@@ -17,6 +17,7 @@ module raises ``ImportError``.
 from __future__ import annotations
 
 import importlib
+import subprocess
 import sys
 from pathlib import Path
 
@@ -133,3 +134,70 @@ def test_compare_runs_turns_a_load_failure_into_a_usage_error(no_driver):
 
     assert info.value.code == cr.EXIT_USAGE
     assert "no_such_module.py" in str(info.value)
+
+
+# --- the category helpers -------------------------------------------------------
+
+
+@pytest.mark.parametrize("name", HELPERS)
+def test_a_category_helper_imports_without_the_src_package(
+    name, no_driver, fresh_helpers
+):
+    module = importlib.import_module(f"scripts.benchmarking.{name}")
+
+    assert module.canonical_source_url.__module__ == "archi_benchmark_provenance"
+    assert module.canonical_source_url(f"{KB}/running-jobs/") == f"{KB}/running-jobs"
+
+
+@pytest.mark.parametrize("name", HELPERS)
+def test_a_category_helper_binds_the_package_function_when_the_driver_is_present(
+    name, fresh_helpers
+):
+    module = importlib.import_module(f"scripts.benchmarking.{name}")
+
+    assert module.canonical_source_url is package_function
+
+
+def test_category_modules_load_without_the_src_package(no_driver, fresh_helpers):
+    category_slice, paired_tests = cr._category_modules()
+
+    assert (
+        category_slice.canonical_source_url.__module__ == "archi_benchmark_provenance"
+    )
+    assert category_slice.canonical_source_url(f"{KB}/x/") == f"{KB}/x"
+    assert paired_tests.canonical_source_url(f"{KB}/x/") == f"{KB}/x"
+
+
+def test_category_modules_load_in_an_isolated_interpreter_from_another_directory(
+    tmp_path,
+):
+    # The issue's repro as a plain script run: no site-packages (-S, so no psycopg2),
+    # no cwd or PYTHONPATH on sys.path (-I), a cwd outside the repository, and only
+    # the script's own directory on sys.path, as `python scripts/benchmarking/...`
+    # has it. Only the script's own shim can make `scripts` and `src` importable.
+    script_dir = Path(cr.__file__).resolve().parent
+    # `_utils_function` goes first: at that point nothing but its own shim has put
+    # the repository root on sys.path, so a shim that forgets to is caught here.
+    probe = (
+        "import sys\n"
+        f"sys.path.insert(0, {str(script_dir)!r})\n"
+        "import compare_runs\n"
+        "loaded = compare_runs._utils_function("
+        "'benchmark_provenance', 'canonical_source_url')\n"
+        "print(loaded.__module__)\n"
+        "category_slice, paired_tests = compare_runs._category_modules()\n"
+        "print(category_slice.canonical_source_url.__module__)\n"
+    )
+    result = subprocess.run(
+        [sys.executable, "-S", "-I", "-c", probe],
+        cwd=tmp_path,
+        capture_output=True,
+        text=True,
+        check=False,
+    )
+
+    assert result.returncode == 0, result.stderr
+    assert result.stdout.split() == [
+        "archi_benchmark_provenance",
+        "archi_benchmark_provenance",
+    ]
````

### Appendix C (task 1.2: the issue's repro, for `/tmp/816-repro.py`)

````python
"""The fasrc/archi#816 repro. Run `python -S -I /tmp/816-repro.py` from the repository root."""

import sys

sys.path.insert(0, ".")

from scripts.benchmarking import compare_runs  # noqa: E402

compare_runs._category_modules()
print("_category_modules(): OK")
for name in ("category_slice", "category_attribution", "paired_tests"):
    __import__(f"scripts.benchmarking.{name}")
    print(f"{name}: import OK")
````

Run `python -S -I /tmp/816-repro.py` from the repository root. Expected output, exactly, and exit 0:

````text
_category_modules(): OK
category_slice: import OK
category_attribution: import OK
paired_tests: import OK
````
