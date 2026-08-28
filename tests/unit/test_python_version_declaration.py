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

from src.cli.managers.base_image_preflight import service_templates

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


# --- Service base-image references (fasrc/archi#266) -----------------------------------
#
# The `service_templates()` set builds on an `a2rchi-*-base` image. Upstream owns
# `docker.io/a2rchi/*` and its `latest` tag floats, so a fork deployment picked up whatever
# upstream last published -- which is how a Python 3.10 base met a `requires-python >=3.11`
# project and broke `pip install .` on every clean host.
#
# The templates that define the base images themselves, and those building on third-party
# images, are out of scope: they have no `a2rchi-*-base` reference to check, so the filter
# below excludes them by construction rather than by name.

_A2RCHI_BASE_RE = re.compile(r"^FROM\s+(?P<ref>\S*a2rchi-\w+-base\S*)", re.MULTILINE)

_EXPECTED_BASE_REGISTRY = "ghcr.io/fasrc/"


# Written above a digest-pinned FROM line by
# `scripts/dev/update_service_base_images.py`. A digest names no build, so this
# annotation is the only place the build name survives.
_MANAGED_ANNOTATION_RE = re.compile(
    r"^#\s*base-image-pin:\s*(?P<build>\S+)\s+"
    r"\(managed by update_service_base_images\.py\)\s*$"
)


def _base_pins(text):
    """Every ``a2rchi-*-base`` pin in one template, as ``(ref, build)``.

    ``build`` is the tag for a tag reference, or the build named by the managed
    annotation above a digest reference. It is ``None`` when nothing names a
    build: an untagged reference, or a digest with no annotation. A digest and
    a tag are alternatives, so the two forms are read differently rather than
    both being split on the last colon.
    """
    lines = text.splitlines()
    pins = []
    for index, line in enumerate(lines):
        match = _A2RCHI_BASE_RE.match(line)
        if not match:
            continue
        ref = match.group("ref")

        if "@" in ref:
            build = None
            # The annotation sits directly above, but a blank line may separate
            # them, exactly as the writing script tolerates.
            back = index - 1
            while back >= 0 and not lines[back].strip():
                back -= 1
            if back >= 0:
                annotation = _MANAGED_ANNOTATION_RE.match(lines[back].strip())
                if annotation:
                    build = annotation.group("build")
        else:
            _, separator, tag = ref.rpartition(":")
            build = tag if separator else None

        pins.append((ref, build))
    return pins


def _a2rchi_base_pins():
    """``(path, ref, build)`` for every base pin under the template directory."""
    pins = []
    for dockerfile in sorted(DOCKERFILE_TEMPLATE_DIR.rglob("Dockerfile*")):
        for ref, build in _base_pins(dockerfile.read_text()):
            pins.append((dockerfile.relative_to(REPO_ROOT), ref, build))
    return pins


def _a2rchi_base_references():
    """Every ``FROM`` reference naming an ``a2rchi-*-base`` image.

    Returns ``(path, ref)``. The pattern captures ``\\S+``, so a template whose ``FROM`` line
    carries trailing whitespace -- several do -- yields a reference without it.
    """
    references = []
    for dockerfile in sorted(DOCKERFILE_TEMPLATE_DIR.rglob("Dockerfile*")):
        for match in _A2RCHI_BASE_RE.finditer(dockerfile.read_text()):
            references.append((dockerfile.relative_to(REPO_ROOT), match.group("ref")))
    return references


def test_service_templates_reference_the_fork_controlled_registry():
    """An upstream-owned base image is not ours to pin, and upstream moved it under us."""
    references = _a2rchi_base_references()

    assert references, (
        f"no `FROM ...a2rchi-*-base...` reference found under {DOCKERFILE_TEMPLATE_DIR} -- "
        f"the guard would pass vacuously"
    )

    offenders = [
        f"{path}: {ref}"
        for path, ref in references
        if not ref.startswith(_EXPECTED_BASE_REGISTRY)
    ]
    assert not offenders, (
        f"service template(s) reference a base image outside {_EXPECTED_BASE_REGISTRY!r}: "
        f"{offenders} -- that registry is not controlled by this fork, so its contents can "
        f"change without a commit here (fasrc/archi#266)"
    )


def test_service_templates_pin_one_explicit_base_tag():
    """A `ghcr.io/fasrc/` reference still floats if its tag is `latest`.

    This is deliberately separate from the registry check above: a guard that tested only the
    registry prefix would pass `ghcr.io/fasrc/a2rchi-python-base:latest` and reintroduce the
    same defect class from a registry we do own.
    """
    pins = _a2rchi_base_pins()
    assert pins, f"no base reference found under {DOCKERFILE_TEMPLATE_DIR}"

    unpinned = [
        f"{path}: {ref}"
        for path, ref, build in pins
        if build is None and "@" not in ref
    ]
    assert not unpinned, (
        f"service template(s) name a base image with no tag, which resolves to `latest`: "
        f"{unpinned}"
    )

    unnamed = [
        f"{path}: {ref}" for path, ref, build in pins if build is None and "@" in ref
    ]
    assert not unnamed, (
        f"service template(s) pin a digest with no `# base-image-pin:` annotation above it: "
        f"{unnamed} -- a digest names no build, so nothing here says which build these "
        f"services are on, and a split pin becomes invisible"
    )

    builds = {}
    for path, _ref, build in pins:
        builds.setdefault(build, []).append(str(path))

    floating = builds.get("latest")
    assert not floating, (
        f"service template(s) pin the floating tag `latest`: {sorted(floating)} -- the image "
        f"behind it can be replaced without a commit here"
    )

    assert len(builds) == 1, (
        f"service templates reference more than one base build: "
        f"{ {build: sorted(paths) for build, paths in builds.items()} } -- a split pin means "
        f"some services build on a different interpreter than others"
    )

    # Every member of the declared service-template set must contribute at least one pin.
    pinned_absolute = {(REPO_ROOT / path).resolve() for path, _, _ in pins}
    no_pin = [p for p in service_templates() if p.resolve() not in pinned_absolute]
    assert not no_pin, (
        f"service template(s) contribute no base pin: {no_pin} -- "
        f"a service template with no a2rchi-*-base reference cannot be covered by the deploy "
        f"preflight"
    )


def test_service_template_without_pin_is_detected(tmp_path):
    """The per-template pin check catches a service template that has no base pin."""
    digest = "sha256:" + "ab" * 32
    (tmp_path / "Dockerfile-with-pin").write_text(
        f"# base-image-pin: dev-abc123 (managed by update_service_base_images.py)\n"
        f"FROM ghcr.io/fasrc/a2rchi-python-base@{digest}\n"
    )
    (tmp_path / "Dockerfile-without-pin").write_text(
        "FROM docker.io/library/python:3.11\n"
    )

    templates = service_templates(tmp_path)
    pinned_absolute = {t.resolve() for t in templates if _base_pins(t.read_text())}
    no_pin = [p for p in templates if p.resolve() not in pinned_absolute]

    assert no_pin == [tmp_path / "Dockerfile-without-pin"]


# --- The same guard, once the templates carry digests (fasrc/archi#334, #335) ----------
#
# A digest reference names no tag, so the check above cannot read a build out of it: it
# takes the text after the last colon, which for `...@sha256:c068...` is the digest hex.
# The python and pytorch bases have different digests by construction, so that check sees
# two "tags" and fails. The build name for a digest-pinned line lives in the managed
# annotation written above it by `scripts/dev/update_service_base_images.py`.


def test_base_pins_reads_a_tag_reference():
    text = "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"
    assert _base_pins(text) == [
        ("ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4", "dev-4314ac4")
    ]


def test_base_pins_reads_the_build_from_a_digest_annotation():
    digest = "sha256:" + "c0" * 32
    text = (
        "# base-image-pin: dev-4314ac4 (managed by update_service_base_images.py)\n"
        f"FROM ghcr.io/fasrc/a2rchi-python-base@{digest}\n"
    )
    assert _base_pins(text) == [
        (f"ghcr.io/fasrc/a2rchi-python-base@{digest}", "dev-4314ac4")
    ]


def test_base_pins_reports_no_build_for_an_unannotated_digest():
    """A digest with no annotation says nothing about which build it is."""
    digest = "sha256:" + "c0" * 32
    text = f"FROM ghcr.io/fasrc/a2rchi-python-base@{digest}\n"
    assert _base_pins(text) == [(f"ghcr.io/fasrc/a2rchi-python-base@{digest}", None)]


def test_base_pins_reports_no_build_for_an_untagged_reference():
    text = "FROM ghcr.io/fasrc/a2rchi-python-base\n"
    assert _base_pins(text) == [("ghcr.io/fasrc/a2rchi-python-base", None)]


def test_base_pins_agree_on_one_build_across_two_different_digests():
    """The whole point: two images, two digests, one build.

    Under the pre-#334 check this is the failing case — it reads the digest hex
    as the tag and sees a split pin where there is none.
    """
    py = "sha256:" + "c0" * 32
    pt = "sha256:" + "b7" * 32
    annotation = (
        "# base-image-pin: dev-4314ac4 (managed by update_service_base_images.py)"
    )
    builds = set()
    for image, digest in (("python", py), ("pytorch", pt)):
        text = f"{annotation}\nFROM ghcr.io/fasrc/a2rchi-{image}-base@{digest}\n"
        builds.update(build for _, build in _base_pins(text))

    assert builds == {"dev-4314ac4"}
