"""The editable install must declare the same flask pins as the base image install.

``src/utils/__init__.py`` imports ``user_service``, which imports
``src.utils.rbac.audit`` -- and importing any submodule of ``src.utils.rbac`` first
runs ``src/utils/rbac/__init__.py``, which imports ``src.utils.rbac.decorators``,
which imports ``flask`` directly. ``requirements/requirements-base.txt`` pins
``flask``, ``flask-login``, ``flask-session``, and ``flask-cors`` (issue #468), but
until those four pins also exist in ``pyproject.toml`` ``[project].dependencies``, a
fresh ``pip install -e .`` with no ``requirements-base.txt`` step never installs
flask at all, and `import src.utils` fails.
"""

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
BASE_REQUIREMENTS_PATH = REPO_ROOT / "requirements" / "requirements-base.txt"

FLASK_PACKAGES = ("flask", "flask-login", "flask-session", "flask-cors")


def _parse_requirements(lines):
    """``Requirement`` objects for every non-blank, non-comment line."""
    requirements = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        requirements.append(Requirement(line))
    return requirements


def _load_pyproject_requirements():
    with PYPROJECT_PATH.open("rb") as f:
        data = tomllib.load(f)
    return _parse_requirements(data["project"]["dependencies"])


def _load_base_requirements():
    return _parse_requirements(BASE_REQUIREMENTS_PATH.read_text().splitlines())


def _exact_pin_version(requirement):
    """The version of a single ``==`` clause, or ``None`` for anything else.

    A requirement with no specifier, a range (``>=``), or more than one clause is
    not an exact pin, and the caller needs to tell that apart from "pinned, but to
    a different version".
    """
    specs = list(requirement.specifier)
    if len(specs) == 1 and specs[0].operator == "==":
        return specs[0].version
    return None


def check_pin_matches(package_name, pyproject_requirements, base_requirements):
    """Compare one package's pin across the two dependency lists.

    Returns ``None`` when ``pyproject.toml`` and ``requirements/requirements-base.txt``
    agree on a single exact pin for ``package_name``, else an error message naming the
    package, the file(s) at fault, and -- on a version mismatch -- both versions.
    Names are compared after PEP 503 canonicalization, so ``Flask-Login`` and
    ``flask_login`` are the same package.
    """
    canonical = canonicalize_name(package_name)

    base_matches = [
        req for req in base_requirements if canonicalize_name(req.name) == canonical
    ]
    if len(base_matches) != 1:
        return (
            f"{package_name}: expected exactly one pin in requirements-base.txt, "
            f"found {len(base_matches)}"
        )
    base_version = _exact_pin_version(base_matches[0])
    if base_version is None:
        return (
            f"{package_name}: requirements-base.txt pin is not a single '==' clause "
            f"({base_matches[0].specifier})"
        )

    pyproject_matches = [
        req
        for req in pyproject_requirements
        if canonicalize_name(req.name) == canonical
    ]
    if not pyproject_matches:
        return f"{package_name}: missing from pyproject.toml [project].dependencies"
    if len(pyproject_matches) != 1:
        return (
            f"{package_name}: declared more than once in pyproject.toml "
            f"[project].dependencies"
        )
    pyproject_version = _exact_pin_version(pyproject_matches[0])
    if pyproject_version is None:
        return (
            f"{package_name}: pyproject.toml specifier is not a single '==' clause "
            f"({pyproject_matches[0].specifier})"
        )

    if pyproject_version != base_version:
        return (
            f"{package_name}: pyproject.toml pins {pyproject_version} but "
            f"requirements-base.txt pins {base_version}"
        )
    return None


def test_flask_pinned_in_pyproject():
    pyproject_requirements = _load_pyproject_requirements()
    base_requirements = _load_base_requirements()
    error = check_pin_matches("flask", pyproject_requirements, base_requirements)
    assert error is None, error


def test_flask_login_pinned_in_pyproject():
    pyproject_requirements = _load_pyproject_requirements()
    base_requirements = _load_base_requirements()
    error = check_pin_matches("flask-login", pyproject_requirements, base_requirements)
    assert error is None, error


def test_flask_session_pinned_in_pyproject():
    pyproject_requirements = _load_pyproject_requirements()
    base_requirements = _load_base_requirements()
    error = check_pin_matches(
        "flask-session", pyproject_requirements, base_requirements
    )
    assert error is None, error


def test_flask_cors_pinned_in_pyproject():
    pyproject_requirements = _load_pyproject_requirements()
    base_requirements = _load_base_requirements()
    error = check_pin_matches("flask-cors", pyproject_requirements, base_requirements)
    assert error is None, error


def test_check_pin_matches_detects_package_missing_from_pyproject():
    base_requirements = _parse_requirements(["flask==3.0.3"])
    pyproject_requirements = _parse_requirements([])

    error = check_pin_matches("flask", pyproject_requirements, base_requirements)

    assert error is not None
    assert "flask" in error
    assert "pyproject.toml" in error


def test_check_pin_matches_detects_version_drift():
    base_requirements = _parse_requirements(["flask==3.0.3"])
    pyproject_requirements = _parse_requirements(["flask==3.0.2"])

    error = check_pin_matches("flask", pyproject_requirements, base_requirements)

    assert error is not None
    assert "3.0.3" in error
    assert "3.0.2" in error


def test_check_pin_matches_detects_zero_pins_in_base_file():
    base_requirements = _parse_requirements([])
    pyproject_requirements = _parse_requirements(["flask==3.0.3"])

    error = check_pin_matches("flask", pyproject_requirements, base_requirements)

    assert error is not None
    assert "flask" in error
    assert "requirements-base.txt" in error


def test_check_pin_matches_detects_two_pins_in_base_file():
    base_requirements = _parse_requirements(["flask==3.0.3", "flask==3.0.4"])
    pyproject_requirements = _parse_requirements(["flask==3.0.3"])

    error = check_pin_matches("flask", pyproject_requirements, base_requirements)

    assert error is not None
    assert "flask" in error
    assert "requirements-base.txt" in error


def test_check_pin_matches_normalizes_a_differently_spelled_name():
    base_requirements = _parse_requirements(["flask-login==0.6.3"])
    pyproject_requirements = _parse_requirements(["Flask_Login==0.6.3"])

    error = check_pin_matches("flask-login", pyproject_requirements, base_requirements)

    assert error is None, error
