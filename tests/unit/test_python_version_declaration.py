"""The declared Python floor must agree with what the project actually enforces.

``pyproject.toml`` names a ``requires-python`` floor and, separately, a
``tool.pyright.pythonVersion`` target. Nothing keeps those two in sync: issue #201 found the
floor understating the target (declared ``>=3.7`` while pyright checked ``3.11`` and
``src/bin/service_benchmark.py`` used 3.10+ ``match`` syntax that cannot even parse below
3.10), which means static analysis has never once checked the declared floor.
"""

import sys
import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _load_pyproject():
    with PYPROJECT_PATH.open("rb") as f:
        return tomllib.load(f)


def _declared_floor(specifier_set):
    lower_bounds = [
        Version(spec.version)
        for spec in specifier_set
        if spec.operator in (">=", "~=", "==")
    ]
    assert lower_bounds, f"no lower-bound specifier found in {specifier_set}"
    return min(lower_bounds)


def test_declared_floor_is_not_below_the_pyright_target():
    data = _load_pyproject()
    requires_python = SpecifierSet(data["project"]["requires-python"])
    pyright_target = Version(data["tool"]["pyright"]["pythonVersion"])

    floor = _declared_floor(requires_python)

    assert floor >= pyright_target, (
        f"declared requires-python floor {floor} is below the pyright target "
        f"{pyright_target} -- static analysis never checks the declared floor"
    )


def test_running_interpreter_satisfies_declared_specifier():
    data = _load_pyproject()
    requires_python = SpecifierSet(data["project"]["requires-python"])
    running_version = Version(
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )

    assert requires_python.contains(running_version), (
        f"running interpreter {running_version} does not satisfy the declared "
        f"requires-python specifier {requires_python}"
    )
