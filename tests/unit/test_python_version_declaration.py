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

# Prose that states a supported Python minimum, with the number of stated minimums each
# anchor is expected to match. A doc that merely records a historical version (the migration
# notes) is out of scope on purpose, so the guard cannot be defeated by rewording an unrelated
# page. The count is a drift tripwire: a page that gains or loses a declaration fails here
# with a pointed message rather than silently going unchecked. Every match is checked against
# the floor, so a file may legitimately state its minimum more than once —
# `openspec/project.md` states it twice, once as a stack entry and once as a constraint.
_DOC_FLOOR_ANCHORS = (
    ("AGENTS.md", re.compile(r"Python (?P<version>\d+\.\d+(?:\.\d+)?)\+"), 1),
    (
        "docs/docs/install.md",
        re.compile(r"`python (?P<version>\d+\.\d+(?:\.\d+)?)\+`"),
        1,
    ),
    (
        "docs/docs/adding_providers.md",
        re.compile(r"Python (?P<version>\d+\.\d+(?:\.\d+)?)\+"),
        1,
    ),
    ("openspec/project.md", re.compile(r"Python (?P<version>\d+\.\d+(?:\.\d+)?)\+"), 2),
)

# Any statement of a Python minimum, in any wording the anchors above accept.
_ANY_FLOOR_RE = re.compile(r"[Pp]ython (?P<version>\d+\.\d+(?:\.\d+)?)\+")


def _ACTIVE_DOC_PAGES():
    """Markdown that states the project's *current* requirements.

    `openspec/changes/` is excluded: proposals and their archive record what was true when
    they were written, so a change that legitimately mentions 3.7 or 3.13 is history, not a
    live declaration a reader would act on.
    """
    pages = sorted(REPO_ROOT.glob("*.md"))
    pages += sorted((REPO_ROOT / "docs").rglob("*.md"))
    pages += sorted((REPO_ROOT / "openspec").glob("*.md"))
    return pages


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

    ``==`` admits a trailing ``.*`` wildcard (``==3.11.*`` means "any 3.11 release"), which
    ``packaging`` hands back verbatim and ``Version`` refuses. Its floor is the version with
    the wildcard dropped, so strip the suffix rather than rejecting the specifier.
    """
    lower_bounds = [
        Version(spec.version.removesuffix(".*"))
        for spec in specifier_set
        if spec.operator in (">=", ">", "~=", "==")
    ]
    assert lower_bounds, f"no lower-bound specifier found in {specifier_set}"
    return max(lower_bounds)


def _declared_specifier():
    return SpecifierSet(_load_pyproject()["project"]["requires-python"])


def _tag_version(tag):
    """The interpreter version an official Python image tag pins, or ``None``.

    Official tags carry the version first and the variant after it — ``3.11-slim``,
    ``3.11-bookworm``, ``3.11.9-slim`` — so the leading numeric component is the
    interpreter. Tags that name no version (``latest``, ``slim-bookworm``) pin nothing this
    guard can check, and are reported as unreadable rather than crashing PEP 440 parsing.
    """
    match = re.match(r"(\d+(?:\.\d+)*)(?:[-.].*)?$", tag)
    return Version(match.group(1)) if match else None


def _pinned_python_base_images():
    """Every ``FROM python:<tag>`` pin in the deployment Dockerfile templates.

    Returns ``(path, tag, version)``; ``version`` is ``None`` for a tag that pins no
    readable interpreter.
    """
    pins = []
    for dockerfile in sorted(DOCKERFILE_TEMPLATE_DIR.rglob("Dockerfile*")):
        for match in _FROM_PYTHON_RE.finditer(dockerfile.read_text()):
            ref = match.group("ref")
            if ":" not in ref:
                continue
            repository, _, tag = ref.rpartition(":")
            if repository.rsplit("/", 1)[-1] != "python":
                continue
            pins.append((dockerfile.relative_to(REPO_ROOT), tag, _tag_version(tag)))
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


def test_declared_floor_accepts_wildcard_equality():
    """``==3.11.*`` is a valid PEP 440 spelling whose effective floor is 3.11.

    ``packaging`` exposes that bound as the string ``"3.11.*"``, which ``Version`` refuses.
    Since ``==`` is in the operator set the helper claims to read, the wildcard form it
    permits must parse too -- otherwise the guard rejects compliant metadata outright
    instead of reading its floor.
    """
    assert _declared_floor(SpecifierSet("==3.11.*")) == Version("3.11")
    assert _declared_floor(SpecifierSet("==3.11")) == Version("3.11")


def test_base_image_tag_variants_expose_their_interpreter_version():
    """Official variant tags pin a readable interpreter; tags without one pin nothing.

    ``python:3.11-slim`` and ``python:3.11-bookworm`` are standard official images that
    satisfy a 3.11 floor. Handing the whole tag to ``Version`` raises ``InvalidVersion``,
    so the guard would block a valid base-image change rather than check its floor.
    """
    assert _tag_version("3.11") == Version("3.11")
    assert _tag_version("3.11-slim") == Version("3.11")
    assert _tag_version("3.11-bookworm") == Version("3.11")
    assert _tag_version("3.11.9-slim") == Version("3.11.9")
    assert _tag_version("latest") is None
    assert _tag_version("slim-bookworm") is None


def test_container_base_images_satisfy_the_declared_floor():
    """A base image below the floor makes `pip install .` fail when the image is built.

    Fifteen service templates build `FROM a2rchi-python-base` and then run `pip install .`,
    so an interpreter under the declared floor turns every CPU deployment into a build
    failure rather than a runtime one.
    """
    requires_python = _declared_specifier()
    pins = _pinned_python_base_images()

    assert pins, f"no `FROM python:<tag>` pin found under {DOCKERFILE_TEMPLATE_DIR}"

    # A readable pin must remain, or retagging every base to `latest` would silently empty
    # the check rather than fail it.
    readable = [
        (path, tag, version) for path, tag, version in pins if version is not None
    ]
    assert readable, (
        f"no `FROM python:<tag>` pin under {DOCKERFILE_TEMPLATE_DIR} names a readable "
        f"interpreter version: {[f'{path}: python:{tag}' for path, tag, _ in pins]}"
    )

    offenders = [
        f"{path}: python:{tag}"
        for path, tag, version in readable
        if not requires_python.contains(version)
    ]
    assert not offenders, (
        f"base image(s) below the declared requires-python floor {requires_python}: "
        f"{offenders} -- `pip install .` rejects the project on these interpreters"
    )


def test_every_page_stating_a_minimum_is_guarded():
    """An unanchored page stating a floor is unguarded, and silently so.

    `_DOC_FLOOR_ANCHORS` is an allowlist, so a page that states a Python minimum without
    being listed there is simply never checked -- the floor can move again and that page
    keeps advertising the old one with every test still green. Corrected pages have been
    left off the list twice during this change alone, so make the omission fail loudly here
    instead of waiting for a reviewer to notice it.
    """
    anchored = {relative_path for relative_path, _, _ in _DOC_FLOOR_ANCHORS}

    unguarded = []
    for path in _ACTIVE_DOC_PAGES():
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        if relative_path in anchored:
            continue
        stated = _ANY_FLOOR_RE.findall(path.read_text())
        if stated:
            unguarded.append(f"{relative_path}: states {stated}")

    assert not unguarded, (
        f"page(s) state a Python minimum but are not in _DOC_FLOOR_ANCHORS, so the floor "
        f"guard never checks them: {unguarded}"
    )


def test_documentation_does_not_state_a_superseded_floor():
    """Contributor- and user-facing docs must not advertise an interpreter pip refuses."""
    requires_python = _declared_specifier()

    offenders = []
    for relative_path, pattern, expected_count in _DOC_FLOOR_ANCHORS:
        text = (REPO_ROOT / relative_path).read_text()
        matches = pattern.findall(text)
        assert len(matches) == expected_count, (
            f"{relative_path}: expected {expected_count} stated Python minimum(s), found "
            f"{matches} -- the guard's anchor needs updating"
        )
        offenders.extend(
            f"{relative_path}: states {stated}+"
            for stated in matches
            if not requires_python.contains(Version(stated))
        )

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
