"""Load a pure-stdlib ``src/utils`` module without the package side effects.

``src/utils/__init__.py`` imports the config service, which imports ``psycopg2``,
so the ordinary ``from src.utils.<module> import ...`` needs a database driver on a
host that is only reading finished benchmark artifacts. ``compare_runs.py`` and the
category helpers it imports on every run (``category_slice.py``,
``category_attribution.py``, ``paired_tests.py``) share this loader instead
(fasrc/archi#816): the package import is tried first, and when it is unavailable
the module's own file is loaded straight from disk. Same file, same function: this
is not a copy.

Only ``src/utils`` modules that are themselves pure stdlib at import can be loaded
this way; today those are ``benchmark_schema.py``, ``benchmark_provenance.py`` and
``judge_gates.py``. This module is standard library only, on purpose: it must
import on a host with no project dependencies at all.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

#: The repository root: ``scripts/benchmarking/stdlib_utils.py`` is two levels down.
REPO_ROOT = Path(__file__).resolve().parents[2]


def project_root_on_path() -> None:
    """Make ``src`` and ``scripts`` importable from a plain ``python scripts/...`` run."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


def utils_module(module_name: str) -> ModuleType:
    """``src.utils.<module_name>``: the package module when it imports, else its file.

    Raises ``ImportError`` when the file cannot be loaded either. The caller decides
    how to report that; ``compare_runs.py`` turns it into a usage exit.
    """
    project_root_on_path()
    try:
        return importlib.import_module(f"src.utils.{module_name}")
    except ImportError:
        pass
    source = REPO_ROOT / "src" / "utils" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"archi_{module_name}", source)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable
        raise ImportError(f"cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ImportError(f"cannot load {source}: {exc}") from None
    return module


def utils_function(module_name: str, name: str) -> Any:
    """A function from a pure-stdlib ``src/utils`` file (see ``utils_module``)."""
    return getattr(utils_module(module_name), name)
