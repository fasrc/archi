"""The declared Python floor must agree with what the project actually enforces.

``pyproject.toml`` names a ``requires-python`` floor and, separately, a
``tool.pyright.pythonVersion`` target. Nothing keeps those two in sync: issue #201 found the
floor understating the target (declared ``>=3.7`` while pyright checked ``3.11`` and
``src/bin/service_benchmark.py`` used 3.10+ ``match`` syntax that cannot even parse below
3.10), which means static analysis has never once checked the declared floor.
"""

import re
import sys
import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
DOCKERFILE_TEMPLATE_DIR = REPO_ROOT / "src" / "cli" / "templates" / "dockerfiles"

# `FROM <registry>/<path>/python:<tag>` — the official CPython image only. The repository
# path is captured separately so `a2rchi-python-base:latest` and `pytorch/pytorch:2.6.0`,
# which pin no interpreter version we can read, are not mistaken for it.
_FROM_PYTHON_RE = re.compile(r"^FROM\s+(?P<ref>\S+)", re.MULTILINE)

# Prose that states a supported Python minimum. Each anchor matches exactly one line in its
# file; a doc that merely records a historical version (the migration notes) is out of scope
# on purpose, so the guard cannot be defeated by rewording an unrelated page.
_DOC_FLOOR_ANCHORS = (
    ("AGENTS.md", re.compile(r"Python (?P<version>\d+\.\d+(?:\.\d+)?)\+")),
    ("docs/docs/install.md", re.compile(r"`python (?P<version>\d+\.\d+(?:\.\d+)?)\+`")),
)


def _load_pyproject():
    with PYPROJECT_PATH.open("rb") as f:
        return tomllib.load(f)


def _declared_floor(specifier_set):
    """The strictest lower bound the specifier imposes.

    ``>`` counts as a lower bound at its own version rather than the next one up: ``>3.10``
    still admits 3.10.1, so treating its floor as 3.10 keeps the comparison conservative.
    The bound is the ``max`` and not the ``min`` because a redundant spelling such as
    ``>=3.7,>=3.11`` is floored by its *tightest* constraint — pip installs nothing below
    3.11 — and reporting 3.7 there would fail the guard on a specifier that is in fact
    compliant.
    """
    lower_bounds = [
        Version(spec.version)
        for spec in specifier_set
        if spec.operator in (">=", ">", "~=", "==")
    ]
    assert lower_bounds, f"no lower-bound specifier found in {specifier_set}"
    return max(lower_bounds)


def _declared_specifier():
    return SpecifierSet(_load_pyproject()["project"]["requires-python"])


def _pinned_python_base_images():
    """Every ``FROM python:<version>`` pin in the deployment Dockerfile templates."""
    pins = []
    for dockerfile in sorted(DOCKERFILE_TEMPLATE_DIR.rglob("Dockerfile*")):
        for match in _FROM_PYTHON_RE.finditer(dockerfile.read_text()):
            ref = match.group("ref")
            if ":" not in ref:
                continue
            repository, _, tag = ref.rpartition(":")
            if repository.rsplit("/", 1)[-1] != "python":
                continue
            pins.append((dockerfile.relative_to(REPO_ROOT), tag))
    return pins


def test_declared_floor_is_not_below_the_pyright_target():
    data = _load_pyproject()
    requires_python = SpecifierSet(data["project"]["requires-python"])
    pyright_target = Version(data["tool"]["pyright"]["pythonVersion"])

    floor = _declared_floor(requires_python)

    assert floor >= pyright_target, (
        f"declared requires-python floor {floor} is below the pyright target "
        f"{pyright_target} -- static analysis never checks the declared floor"
    )


def test_declared_floor_accepts_a_bounded_respelling_of_the_same_floor():
    pyright_target = Version("3.11")

    for spec_str in (">=3.11,<4", "~=3.11"):
        floor = _declared_floor(SpecifierSet(spec_str))

        assert floor >= pyright_target, (
            f"specifier {spec_str!r} floors at {pyright_target} but the parsed "
            f"floor {floor} was reported as lower"
        )


def test_declared_floor_uses_the_strictest_lower_bound():
    """A redundant respelling is floored by its tightest constraint, not its loosest."""
    assert _declared_floor(SpecifierSet(">=3.7,>=3.11")) == Version("3.11")
    assert _declared_floor(SpecifierSet(">3.10,>=3.9")) == Version("3.10")


def test_container_base_images_satisfy_the_declared_floor():
    """A base image below the floor makes `pip install .` fail when the image is built.

    Fifteen service templates build `FROM a2rchi-python-base` and then run `pip install .`,
    so an interpreter under the declared floor turns every CPU deployment into a build
    failure rather than a runtime one.
    """
    requires_python = _declared_specifier()
    pins = _pinned_python_base_images()

    assert pins, f"no `FROM python:<version>` pin found under {DOCKERFILE_TEMPLATE_DIR}"

    offenders = [
        f"{path}: python:{tag}"
        for path, tag in pins
        if not requires_python.contains(Version(tag))
    ]
    assert not offenders, (
        f"base image(s) below the declared requires-python floor {requires_python}: "
        f"{offenders} -- `pip install .` rejects the project on these interpreters"
    )


def test_documentation_does_not_state_a_superseded_floor():
    """Contributor- and user-facing docs must not advertise an interpreter pip refuses."""
    requires_python = _declared_specifier()

    offenders = []
    for relative_path, pattern in _DOC_FLOOR_ANCHORS:
        text = (REPO_ROOT / relative_path).read_text()
        matches = pattern.findall(text)
        assert len(matches) == 1, (
            f"{relative_path}: expected exactly one stated Python minimum, found "
            f"{matches} -- the guard's anchor needs updating"
        )
        if not requires_python.contains(Version(matches[0])):
            offenders.append(f"{relative_path}: states {matches[0]}+")

    assert not offenders, (
        f"documentation states a Python minimum below the declared floor "
        f"{requires_python}: {offenders}"
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
