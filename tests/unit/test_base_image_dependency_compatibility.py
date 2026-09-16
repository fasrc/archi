"""Pins in the base-image requirement sets must be installable together.

``scripts/dev/build_docker_images.sh`` builds each base image's requirement set by
concatenating a header with ``requirements/requirements-base.txt``. Neither file
knows what the other pins, so two independently reasonable pins can produce a set
``pip`` cannot resolve — and the only build path that assembles the GPU set is the
release workflow (#473), so the failure surfaces during a release rather than on a
PR.

That is not hypothetical. It happened on 2026-09-15 (#472): PR #453 added
``opentelemetry-sdk==1.44.0`` to the shared base while
``gpu-requirementsHEADER.txt`` pinned ``vllm==0.8.5``, whose metadata requires
``opentelemetry-sdk<1.27.0``. The first ``v2026.08.0`` release dispatch died with
``ResolutionImpossible`` in the build step.

These guards encode the pairwise constraints that a reader of either file alone
cannot see. They are deliberately offline and data-driven: each constraint carries
the date it was measured from PyPI metadata, because a guard asserting a
dependency fact without saying where the fact came from is unmaintainable. Re-measure
before changing a table here.

Known boundary, and it is a large one: **these guards do not prove the set resolves.**
Each encodes one pairwise constraint somebody already knew to look for, so the module
is green exactly when the known traps are absent — not when ``pip`` succeeds. Fixing
#472 demonstrated the gap: the vllm guard below went green while the real GPU set
still failed ``ResolutionImpossible``, because ``torch==2.7.0`` also needs
``sympy>=1.13.3`` and the shared base pinned ``1.13.1``. Almost every constraint here
was added *after* a resolver found it; ``VLLM_TORCH_SPEC`` is the one exception, added
on 2026-09-16 from a review reading of the GPU header, which had asserted the vllm-torch
coupling in prose for a day with no table behind it.

So this module is a regression net, not a gate. The gate is resolving both generated
files, which catches transitive conflicts nobody predicted and costs seconds:

    pip install --dry-run -r src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt
    pip install --dry-run -r src/cli/templates/dockerfiles/base-python-image/requirements.txt

Neither the net nor the resolve proves the packages *import*, and that is not a
theoretical caveat either. Building the GPU image on 2026-09-15 — the first successful
build of it since PR #453 — produced an image that resolved cleanly and then died on
``import vllm``:

    ValueError: 'aimv2' is already used by a Transformers config, pick another name.

``transformers`` was unpinned, vllm 0.9.0 declares no ceiling on it, and every
transformers release from 4.54.0 on collides with vllm's own ``aimv2`` shim. **No
resolver can reach that conclusion**, because every colliding version satisfies the
declared range. ``VLLM_TRANSFORMERS_SPEC`` below is therefore the one table here NOT
measured from ``requires_dist`` — it was measured by importing vllm in a built image.

So the tiers are: this module catches known pairwise traps, a resolve catches
unpredicted version conflicts, and only a real image build catches import-time
collisions. #473 asks CI for the last two. Until it has them, the release dispatch is
the first thing that builds the GPU image.
"""

import re
from pathlib import Path

import pytest
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parents[2]

CPU_HEADER = REPO_ROOT / "requirements" / "cpu-requirementsHEADER.txt"
GPU_HEADER = REPO_ROOT / "requirements" / "gpu-requirementsHEADER.txt"
BASE_REQUIREMENTS = REPO_ROOT / "requirements" / "requirements-base.txt"

# The two sets ``pip`` actually installs. ``build_docker_images.sh`` writes each one by
# concatenating a header with the shared base, so these are the only files where a pin
# duplicated ACROSS the header/base boundary is visible at all.
_DOCKERFILE_TEMPLATES = REPO_ROOT / "src" / "cli" / "templates" / "dockerfiles"
GENERATED_CPU_REQUIREMENTS = (
    _DOCKERFILE_TEMPLATES / "base-python-image" / "requirements.txt"
)
GENERATED_GPU_REQUIREMENTS = (
    _DOCKERFILE_TEMPLATES / "base-pytorch-image" / "requirements.txt"
)
PYTORCH_BASE_DOCKERFILE = (
    REPO_ROOT
    / "src"
    / "cli"
    / "templates"
    / "dockerfiles"
    / "base-pytorch-image"
    / "Dockerfile"
)

# ``FROM docker.io/pytorch/pytorch:2.7.0-cuda12.6-cudnn9-devel`` -> ("2.7.0", "12.6")
_PYTORCH_FROM_PATTERN = re.compile(
    r"^FROM\s+\S*pytorch/pytorch:(\d+(?:\.\d+)*)-cuda(\d+(?:\.\d+)*)", re.MULTILINE
)

# Every table below maps a measured version STRING to the specifier that version
# actually DECLARES, verbatim as PyPI reports it — so a row can be diffed against
# ``requires_dist`` by eye, and ``packaging`` rather than this module decides what it
# means. ``None`` means the version declares no dependency on that package at all, which
# is different from the version being unmeasured — see ``_measured``.
#
# The keys are full version strings, post identifier included, because a post release is
# a separate distribution with its own metadata. Review on 2026-09-16 found the keys were
# numeric release tuples, which merged ``0.0.29``, ``0.0.29.post1``, ``0.0.29.post2`` and
# ``0.0.29.post3`` into one row holding whichever constraint was measured last — and the
# constraint it held, ``torch==2.6.0``, was wrong for two of the four.
#
# The uniform shape is deliberate. The first version of this module used one-sided
# comparisons — "vllm at least 0.9.0", "sympy at least the floor" — and review on
# 2026-09-15 found three separate holes in that shortcut, because dependency
# constraints are neither monotonic across releases nor always one-sided:
#
#   * vllm 0.19.0 RAISED its floor to 1.27.0, so "newer vllm is always fine" is false.
#   * vllm 0.9.0 still has a floor of 1.26.0, so an SDK DOWNGRADE breaks it.
#   * torch 2.6.0 pins sympy EXACTLY, so a higher sympy is as wrong as a lower one.
#
# Unknown releases fail rather than pass. A guard that silently accepts an unmeasured
# version is worse than no guard: it reports confidence it does not have.

# vllm -> its ``opentelemetry-sdk`` specifier. Measured from PyPI ``requires_dist``
# on 2026-09-15.
#
# The trap behind #472 is that the only range vllm 0.8.5 accepts is itself
# uninstallable: that suite's ``opentelemetry-instrumentation`` imports
# ``pkg_resources`` at module scope, which setuptools 82 removed (see
# ``requirements-base.txt``). So no SDK pin satisfies both vllm 0.8.5 and a working
# exporter, and the fix had to be the vllm bump.
# Re-measured on 2026-09-16 when the keys moved to full version strings. The 0.9.x line
# is why the keys matter here too: 0.9.0 and 0.9.1 declare a floor, 0.9.2 declares no
# opentelemetry-sdk dependency at all, and a release-tuple key could not tell 0.9.0 from
# 0.9.0.1.
VLLM_OTEL_SPEC = {
    "0.8.5": ">=1.26.0,<1.27.0",
    "0.8.5.post1": ">=1.26.0,<1.27.0",
    "0.9.0": ">=1.26.0",
    "0.9.0.1": ">=1.26.0",
    "0.9.1": ">=1.26.0",
    "0.9.2": None,
    "0.10.0": None,
    "0.11.0": None,
    "0.12.0": None,
    "0.15.0": None,
    "0.19.0": ">=1.27.0",
    "0.22.0": ">=1.27.0",
    "0.25.0": ">=1.27.0",
    "0.27.0": ">=1.27.0",
    "0.29.0": ">=1.27.0",
}

# ``xformers`` -> its ``torch`` specifier. One exact torch per distribution, so a torch
# bump that leaves xformers behind conflicts rather than falling back.
#
# Re-measured from PyPI ``requires_dist`` on 2026-09-16, which CORRECTED this table. It
# previously read ``(0, 0, 29) -> torch==2.6.0``; that is the 0.0.29.post2 and post3
# constraint, while 0.0.29 and 0.0.29.post1 require ``torch==2.5.1``. xformers rebuilds a
# release against a newer torch under a post tag, so the post identifier is exactly the
# part that carries the constraint — 0.0.33 wants torch 2.9.0 and 0.0.33.post2 wants
# 2.9.1. Note 0.0.32 has no bare release on PyPI; post1 is the first.
XFORMERS_TORCH_SPEC = {
    "0.0.29": "==2.5.1",
    "0.0.29.post1": "==2.5.1",
    "0.0.29.post2": "==2.6.0",
    "0.0.29.post3": "==2.6.0",
    "0.0.30": "==2.7.0",
    "0.0.31": "==2.7.1",
    "0.0.31.post1": "==2.7.1",
    "0.0.32.post1": "==2.8.0",
    "0.0.32.post2": "==2.8.0",
    "0.0.33": "==2.9.0",
    "0.0.33.post1": "==2.9.0",
    "0.0.33.post2": "==2.9.1",
}

# vllm -> its ``torch`` specifier. Always exact, and every release so far moved it, so
# vllm and torch can never be bumped apart. The GPU header already said so in prose;
# review on 2026-09-16 pointed out that no table enforced it, which left the header
# asserting a coupling the guards ignored. Measured from PyPI ``requires_dist`` on
# 2026-09-16.
#
# Note how little of this a version comparison could express: the pin moves on nearly
# every release (0.9.x -> 2.7.0, 0.10.0 -> 2.7.1, 0.11.0 -> 2.8.0) and then sits still
# across two (0.22.0 and 0.25.0 both want 2.11.0; 0.27.0 and 0.29.0 both want 2.13.0).
VLLM_TORCH_SPEC = {
    "0.8.5": "==2.6.0",
    "0.8.5.post1": "==2.6.0",
    "0.9.0": "==2.7.0",
    "0.9.0.1": "==2.7.0",
    "0.9.1": "==2.7.0",
    "0.9.2": "==2.7.0",
    "0.10.0": "==2.7.1",
    "0.11.0": "==2.8.0",
    "0.12.0": "==2.9.0",
    "0.15.0": "==2.9.1",
    "0.19.0": "==2.10.0",
    "0.22.0": "==2.11.0",
    "0.25.0": "==2.11.0",
    "0.27.0": "==2.13.0",
    "0.29.0": "==2.13.0",
}

# vllm -> the ``transformers`` range it actually WORKS with, which is narrower than the
# range it declares. This table is the one exception to "measured from requires_dist":
# vllm 0.9.0 declares only ``transformers>=4.51.1``, with no ceiling, but transformers
# 4.54.0 added a native ``aimv2`` config and vllm 0.9.0 registers its own shim under
# that name, so ``import vllm`` raises at module scope:
#
#   ValueError: 'aimv2' is already used by a Transformers config, pick another name.
#
# Measured by importing vllm inside the built GPU image on 2026-09-15: 4.52.4 and
# 4.53.3 import, 4.54.1 / 4.55.4 / 4.56.2 all carry native aimv2. **No resolver can
# find this** — every one of those versions satisfies the declared range. It took a
# real image build, which is why #473 matters and why an unpinned transformers is a
# latent break rather than a convenience.
VLLM_TRANSFORMERS_SPEC = {
    "0.9.0": ">=4.51.1,<4.54.0",
}

# ``torch`` -> its ``sympy`` specifier. This coupling crosses the header/base boundary
# (torch is pinned in the headers, sympy in the shared base), so neither file shows it
# alone — which is how the first pass at #472 missed it. Re-measured 2026-09-16; torch
# 2.6.0's declaration carries a ``python_version >= "3.9"`` marker, which every image
# here satisfies, so the clause is recorded unconditionally.
TORCH_SYMPY_SPEC = {
    "2.6.0": "==1.13.1",
    "2.7.0": ">=1.13.3",
    "2.7.1": ">=1.13.3",
    "2.8.0": ">=1.13.3",
}


_MISSING = object()


def _normalize_version(version: str) -> str:
    """Casefold and strip a version string, so it can be a table key or compared."""
    return version.strip().lower()


def _measured(table: dict, version: str):
    """The specifier ``table`` records for ``version``, or ``_MISSING``.

    ``_MISSING`` means nobody measured this version, and every caller turns that into
    a failure. It is deliberately distinct from a recorded ``None``, which means the
    version was measured and declares no dependency on that package at all. Collapsing
    the two would let an unmeasured release read as "declares nothing" and pass.
    """
    return table.get(_normalize_version(version), _MISSING)


def _satisfies(candidate: str, specifier: str) -> bool:
    """Does version ``candidate`` satisfy the declared ``specifier``?

    ``packaging`` decides, because PEP 440 is not what a numeric comparison does.
    Zero-padding makes ``1.27`` equal ``1.27.0``; an exclusive ordered bound excludes
    a pre-release of its own version, so ``<1.27.0`` rejects ``1.27.0rc1``; and ``==``
    rejects a post release of its version. A hand-rolled comparison got all three
    wrong in this module before 2026-09-16.

    ``prereleases=True`` because every candidate here comes from an explicit ``==``
    pin in a requirement file, which is the case where pip also considers a
    pre-release. An unparseable specifier raises ``InvalidSpecifier`` rather than
    silently passing, so a typo in a measured table cannot weaken a guard.
    """
    return SpecifierSet(specifier).contains(Version(candidate), prereleases=True)


# ``markitdown[pdf,pptx]==0.1.5`` -> name "markitdown". An extras marker belongs to the
# requirement, not to the project name, so both patterns step over it. The trailing ``$``
# on the pin pattern is deliberate: it makes a COMPOUND specifier such as
# ``vllm==0.9.0,<0.10`` fail to read as an exact pin rather than reading as an exact pin
# of 0.9.0 that the second clause silently contradicts.
_NAME = r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?"
_PIN_PATTERN = re.compile(rf"^{_NAME}==([^\s;#,]+)$")
_REQUIREMENT_PATTERN = re.compile(rf"^{_NAME}\s*(.*)$")

# Packages whose pin a guard in this module reads. Each guard SKIPS when its subject is
# absent from the file, which is correct for a genuinely absent package — the CPU header
# carries no vllm. For these names, therefore, "absent" must mean absent and never
# "present, but not as an exact pin"; ``_unpinned_protected`` enforces the difference.
PROTECTED_PACKAGES = frozenset(
    {
        "opentelemetry-sdk",
        "sympy",
        "torch",
        "transformers",
        "vllm",
        "xformers",
    }
)


def _normalize_name(name: str) -> str:
    """PEP 503 name normalization, so ``opentelemetry_sdk`` and ``Torch`` compare."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_lines(text: str):
    """Yield the requirement lines of ``text``, without comments or option lines.

    Blanks, ``#`` comments and option lines such as ``--extra-index-url`` carry no
    requirement. A trailing comment and an environment marker are cut away, so the
    caller sees the requirement and nothing else.
    """
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        line = line.split("#", 1)[0].split(";", 1)[0].strip()
        if line:
            yield line


def _parse_pins(text: str) -> dict:
    """Map normalized project name to pinned version for every ``name==version``.

    Only exact pins are read: this module asserts against exact pins, and a range
    would make every constraint below ambiguous. ``_unpinned_protected`` is what keeps
    that ambiguity from turning into a silent skip.
    """
    pins = {}
    for line in _requirement_lines(text):
        match = _PIN_PATTERN.match(line)
        if match:
            pins[_normalize_name(match.group(1))] = match.group(2)
    return pins


# A requirement given as a bare VCS or URL reference rather than a project name:
# ``git+https://host/repo.git#egg=vllm``, ``hg+``, ``https://host/pkg.whl``. Review on
# 2026-09-16: ``_requirement_lines`` strips the ``#egg=vllm`` fragment as a comment and
# the name matcher then recorded the project as ``git``, so every vllm guard read vllm
# as ABSENT and skipped while pip installed an arbitrary checkout. These lines do not
# start with ``-``, so the directive matcher above never saw them either.
_VCS_OR_URL_REQUIREMENT = re.compile(
    r"^(?:(?:git|hg|bzr|svn)\+|https?://|file://|[A-Za-z]:[\\/])", re.IGNORECASE
)


def _opaque_requirements(text: str) -> list:
    """Requirement lines whose project name this module cannot read.

    Reported rather than parsed. A VCS or URL requirement supplies a real package with
    no version this module can check, so it must fail the suite instead of resolving to
    a nonsense name like ``git``.
    """
    return [
        line for line in _requirement_lines(text) if _VCS_OR_URL_REQUIREMENT.match(line)
    ]


def _parse_requirements(text: str) -> dict:
    """Map normalized project name to specifier text for EVERY requirement line.

    Operator-agnostic, unlike ``_parse_pins``, so a protected package written as a
    range stays visible instead of vanishing. That difference is the whole point.
    """
    found = {}
    for line in _requirement_lines(text):
        match = _REQUIREMENT_PATTERN.match(line)
        if match:
            found[_normalize_name(match.group(1))] = match.group(2).strip()
    return found


# ``-r other.txt`` / ``-c constraints.txt`` and their long forms. Deliberately NOT
# ``--extra-index-url`` or ``--index-url``, which carry no requirements and which the
# CPU header legitimately uses.
# Options that supply installable content without a version this module can read.
# ``-r``/``-c`` pull in another file; ``-e``/``--editable`` install a path or VCS
# checkout directly. Adversarial review on 2026-09-16 found only the first pair was
# matched, so ``-e git+...#egg=vllm`` was dropped by ``_requirement_lines`` (it starts
# with ``-``) and reported by nothing — every protected-dependency guard then read vllm
# as ABSENT and skipped, admitting an arbitrary unmeasured checkout to the GPU image.
#
# The rule is the class, not the two spellings: an option that can carry a requirement
# must fail the suite rather than read as silence.
# The short forms take a value with NO separator too: pip accepts ``-rother.txt`` and
# ``-cconstraints.txt`` exactly as it accepts ``-r other.txt``. Review on 2026-09-16
# found the pattern demanded whitespace, ``=`` or end-of-line, so the compact spelling
# matched nothing while ``_requirement_lines`` still dropped the line for starting with
# ``-`` — the included file's pins stayed invisible.
_REQUIREMENT_BEARING_PATTERN = re.compile(
    r"^(?:-[rce]|--requirement|--constraint|--editable)(?:[=\s]|$|\S)"
)


def _requirement_bearing_directives(text: str) -> list:
    """Option lines of ``text`` that install something this module cannot version.

    These are the lines ``_requirement_lines`` cannot honour: it skips everything
    starting with ``-``, so the package they supply is invisible to every guard while
    pip installs it regardless. Reporting them is what turns that silence into a
    failure.
    """
    directives = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if _REQUIREMENT_BEARING_PATTERN.match(line):
            directives.append(line)
    return directives


# Kept as the previous name so existing guards keep reading; the behaviour is now the
# wider class above.
_include_directives = _requirement_bearing_directives


def _pytorch_from_stages(content: str) -> list:
    """Every ``(torch, cuda)`` pair named by a pytorch ``FROM`` in ``content``.

    A list, not a first match. ``re.search`` returned only the earliest stage, so in a
    multi-stage Dockerfile a builder stage on the correct torch could mask a later
    stage on the wrong one while ``pip install -r requirements.txt`` ran against the
    mismatched base (adversarial review, 2026-09-16).
    """
    return [
        (match.group(1), match.group(2))
        for match in _PYTORCH_FROM_PATTERN.finditer(content)
    ]


def _declarations(text: str) -> dict:
    """Map normalized project name to EVERY specifier declared for it, in file order.

    ``_parse_pins`` and ``_parse_requirements`` both keep only the last occurrence of a
    name, which is what let a repeated declaration hide a contradiction. This keeps all
    of them, so a duplicate is a fact the guards can see.
    """
    found = {}
    for line in _requirement_lines(text):
        match = _REQUIREMENT_PATTERN.match(line)
        if match:
            found.setdefault(_normalize_name(match.group(1)), []).append(
                match.group(2).strip()
            )
    return found


def _conditional_protected(text: str) -> dict:
    """Protected packages declared with an environment marker, mapped to that marker.

    An empty mapping is the healthy answer. ``_requirement_lines`` cuts a line at
    ``;`` so the guards never see the marker, which means a line pip may skip on this
    image reads here as an unconditional pin.
    """
    conditional = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        line = line.split("#", 1)[0].strip()
        requirement, separator, marker = line.partition(";")
        if not separator:
            continue
        match = _REQUIREMENT_PATTERN.match(requirement.strip())
        if match and _normalize_name(match.group(1)) in PROTECTED_PACKAGES:
            conditional[_normalize_name(match.group(1))] = marker.strip()
    return conditional


def _duplicated_protected(text: str) -> dict:
    """Protected packages that ``text`` declares more than once, with every specifier.

    An empty mapping is the healthy answer. Any entry means pip sees two constraints
    where the guards see one, so the set can be impossible while this module is green.
    """
    return {
        name: specifiers
        for name, specifiers in _declarations(text).items()
        if name in PROTECTED_PACKAGES and len(specifiers) > 1
    }


def _unpinned_protected(text: str) -> dict:
    """Protected packages that ``text`` names without an exact ``==`` pin.

    An empty mapping is the healthy answer. Any entry means a guard below would
    ``skip`` on a package the file genuinely carries.
    """
    pins = _parse_pins(text)
    return {
        name: specifier
        for name, specifier in _parse_requirements(text).items()
        if name in PROTECTED_PACKAGES and name not in pins
    }


def _pins(path: Path) -> dict:
    """``_parse_pins`` over a file, for the fixtures below."""
    return _parse_pins(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cpu_pins():
    return _pins(CPU_HEADER)


@pytest.fixture(scope="module")
def gpu_pins():
    return _pins(GPU_HEADER)


@pytest.fixture(scope="module")
def base_pins():
    return _pins(BASE_REQUIREMENTS)


class TestVllmAcceptsThePinnedOpenTelemetrySdk:
    """The GPU set pins both ``vllm`` and, via the shared base, ``opentelemetry-sdk``.

    Only the GPU header carries ``vllm``, so this conflict can only ever appear in
    the PyTorch image — the one image no pre-merge job builds (#473).
    """

    def test_vllm_does_not_cap_the_pinned_opentelemetry_sdk(self, gpu_pins, base_pins):
        vllm = gpu_pins.get("vllm")
        sdk = base_pins.get("opentelemetry-sdk")
        if vllm is None or sdk is None:
            pytest.skip(
                "this guard only applies while the GPU header pins vllm and the "
                "shared base pins opentelemetry-sdk"
            )

        specifier = _measured(VLLM_OTEL_SPEC, vllm)
        if specifier is _MISSING:
            pytest.fail(
                f"vllm {vllm} is not in VLLM_OTEL_SPEC. Read its ``requires_dist`` on "
                f"PyPI, add the row with today's date, then re-run. Do not widen this "
                f"to a version comparison: vllm 0.19.0 RAISED its opentelemetry-sdk "
                f"floor to 1.27.0, so 'newer is always safe' is false and an "
                f"unmeasured release can reintroduce #472."
            )

        if specifier is None:
            return  # this vllm declares no opentelemetry-sdk dependency at all

        assert _satisfies(sdk, specifier), (
            f"vllm {vllm} requires opentelemetry-sdk{specifier}, but "
            f"requirements-base.txt pins opentelemetry-sdk=={sdk}. pip cannot resolve "
            f"the PyTorch image's requirement set, so the release build fails at the "
            f"build step (#472). Note the constraint has a FLOOR as well as any "
            f"ceiling, so downgrading the SDK is not a fix — and for vllm 0.8.5 the "
            f"whole accepted range is uninstallable on current setuptools, as "
            f"requirements-base.txt records."
        )


class TestBothBaseImagesAgreeOnTorch:
    """The CPU and GPU images must pin the same ``torch``.

    Decided 2026-09-15 while fixing #472. The conflict lived only in the GPU header,
    so the minimal fix would have moved that one and left the images on different
    torch versions — while the CPU image runs the embedding and reranking work. They
    move together instead, so a reader never has to wonder which image a torch-shaped
    bug came from.
    """

    def test_cpu_and_gpu_headers_pin_the_same_torch(self, cpu_pins, gpu_pins):
        cpu_torch = cpu_pins.get("torch")
        gpu_torch = gpu_pins.get("torch")
        assert cpu_torch is not None, "cpu-requirementsHEADER.txt must pin torch"
        assert gpu_torch is not None, "gpu-requirementsHEADER.txt must pin torch"
        assert cpu_torch == gpu_torch, (
            f"cpu-requirementsHEADER.txt pins torch=={cpu_torch} and "
            f"gpu-requirementsHEADER.txt pins torch=={gpu_torch}. Both base images "
            f"must carry the same torch: the CPU image runs embedding and reranking, "
            f"so a divergence makes a torch-shaped bug depend on which image served "
            f"the request. Move both, or record here why they diverge."
        )


class TestXformersMatchesTheTorchPin:
    """``xformers`` pins one exact ``torch`` per release, so the two move together."""

    def test_xformers_release_matches_the_pinned_torch(self, gpu_pins):
        xformers = gpu_pins.get("xformers")
        torch = gpu_pins.get("torch")
        if xformers is None or torch is None:
            pytest.skip("this guard only applies while the GPU header pins both")

        specifier = _measured(XFORMERS_TORCH_SPEC, xformers)
        if specifier is _MISSING:
            pytest.fail(
                f"xformers {xformers} is not in XFORMERS_TORCH_SPEC. Read its "
                f"``requires_dist`` on PyPI, add the row with today's date, then "
                f"re-run. Do not delete this guard to get past it: xformers pins "
                f"torch exactly, so an unverified pair is a build failure waiting "
                f"for the next release (#472). Add the row under the FULL version, "
                f"post identifier included: 0.0.29 and 0.0.29.post2 are different "
                f"distributions built against different torch versions."
            )

        assert _satisfies(torch, specifier), (
            f"xformers {xformers} requires torch{specifier}, but the GPU "
            f"header pins torch=={torch}. xformers ships one build per torch "
            f"release, so this set cannot resolve."
        )


class TestVllmMatchesTheTorchPin:
    """``vllm`` pins ``torch`` exactly, and until 2026-09-16 nothing checked it.

    The GPU header states the constraint in its own comment — "vllm pins torch exactly,
    and xformers ships one build per torch release" — but only the xformers half of that
    sentence had a table. So the header asserted a coupling the guards did not enforce.

    Review on 2026-09-16 raised it with the right example: vllm 0.10.0 requires
    ``torch==2.7.1``, so bumping vllm to 0.10.0 and leaving ``torch==2.7.0`` produces a
    set pip cannot resolve. Measured from PyPI ``requires_dist`` on 2026-09-16.

    Honest note on how reachable that was before this guard existed: a bump to 0.10.0
    would still have failed, because ``VLLM_TRANSFORMERS_SPEC`` holds only 0.9.0 and
    fails closed on anything else. That protection is incidental, not designed — the
    transformers table exists to catch an import collision, not to gate torch — and it
    dissolves the moment somebody follows that failure message, measures the transformers
    range for the new vllm and adds the row. At that point torch is unguarded again. A
    constraint the header names deserves its own table rather than a side effect of a
    different one.
    """

    def test_vllm_release_matches_the_pinned_torch(self, gpu_pins):
        vllm = gpu_pins.get("vllm")
        torch = gpu_pins.get("torch")
        if vllm is None or torch is None:
            pytest.skip("this guard only applies while the GPU header pins both")

        specifier = _measured(VLLM_TORCH_SPEC, vllm)
        if specifier is _MISSING:
            pytest.fail(
                f"vllm {vllm} is not in VLLM_TORCH_SPEC. Read its ``requires_dist`` on "
                f"PyPI, add the row with today's date, then re-run. vllm pins torch "
                f"exactly and every release so far moved that pin, so an unmeasured "
                f"release is a resolution failure waiting for the next bump (#472)."
            )

        assert _satisfies(torch, specifier), (
            f"vllm {vllm} requires torch{specifier}, but "
            f"gpu-requirementsHEADER.txt pins torch=={torch}. vllm pins torch exactly, "
            f"so this set cannot resolve. torch, vllm and xformers move as one unit: "
            f"changing any one of the three means re-measuring the other two."
        )

    @pytest.mark.parametrize(
        "vllm_version, torch_version",
        [
            ("0.8.5", "2.6.0"),
            ("0.8.5.post1", "2.6.0"),
            ("0.9.0", "2.7.0"),
            ("0.9.0.1", "2.7.0"),
            ("0.9.1", "2.7.0"),
            ("0.9.2", "2.7.0"),
            ("0.10.0", "2.7.1"),
            ("0.11.0", "2.8.0"),
            ("0.12.0", "2.9.0"),
            ("0.15.0", "2.9.1"),
            ("0.19.0", "2.10.0"),
            ("0.22.0", "2.11.0"),
            ("0.25.0", "2.11.0"),
            ("0.27.0", "2.13.0"),
            ("0.29.0", "2.13.0"),
        ],
    )
    def test_each_measured_vllm_release_accepts_its_own_torch(
        self, vllm_version, torch_version
    ):
        specifier = _measured(VLLM_TORCH_SPEC, vllm_version)
        assert specifier is not _MISSING, (
            f"vllm {vllm_version} is measured in this test but missing from "
            f"VLLM_TORCH_SPEC."
        )
        assert _satisfies(torch_version, specifier), (
            f"vllm {vllm_version} requires torch=={torch_version} as measured, but "
            f"the table says torch{specifier}."
        )

    @pytest.mark.parametrize(
        "vllm_version, torch_version, why",
        [
            (
                "0.10.0",
                "2.7.0",
                "the scenario review raised: bumping vllm one release while leaving "
                "torch behind. 0.10.0 requires torch==2.7.1",
            ),
            (
                "0.9.0",
                "2.7.1",
                "and the mirror image: moving torch forward while vllm stays at 0.9.0, "
                "which requires torch==2.7.0 exactly",
            ),
            (
                "0.29.0",
                "2.7.0",
                "a large vllm jump with torch untouched is the same failure, louder",
            ),
        ],
    )
    def test_a_vllm_bump_that_leaves_torch_behind_is_rejected(
        self, vllm_version, torch_version, why
    ):
        specifier = _measured(VLLM_TORCH_SPEC, vllm_version)
        assert specifier is not _MISSING, f"vllm {vllm_version} must be measured"
        assert not _satisfies(torch_version, specifier), why


class TestPytorchBaseImageMatchesTheTorchPin:
    """The ``FROM`` image already ships a torch, and ``pip`` then installs the pin.

    When the two disagree, ``pip`` replaces the base image's torch with a PyPI wheel
    while the image keeps the older CUDA and cuDNN system libraries underneath. The
    build still succeeds, so nothing fails until a GPU import or a kernel launch —
    and ``vllm`` and ``xformers`` both carry native extensions compiled against one
    specific torch and CUDA pair.

    This guard needs no measured table and no network: both values live in this
    repository, so it cannot rot the way ``XFORMERS_TORCH_SPEC`` and
    ``TORCH_SYMPY_SPEC`` can. It compares only the torch version. Whether the
    image's CUDA matches the ``nvidia-*-cu12`` wheels the set resolves to is a
    resolver question, not a text question — see the module docstring.
    """

    def test_from_image_declares_the_pinned_torch(self, gpu_pins):
        torch = gpu_pins.get("torch")
        assert torch is not None, "gpu-requirementsHEADER.txt must pin torch"

        content = PYTORCH_BASE_DOCKERFILE.read_text(encoding="utf-8")
        stages = _pytorch_from_stages(content)
        assert stages, (
            f"{PYTORCH_BASE_DOCKERFILE} has no recognizable "
            f"``FROM .../pytorch/pytorch:<torch>-cuda<ver>`` line. If the base image "
            f"moved to a different publisher, update _PYTORCH_FROM_PATTERN rather "
            f"than deleting this guard."
        )
        assert len(stages) == 1, (
            f"{PYTORCH_BASE_DOCKERFILE} names {len(stages)} pytorch base stages "
            f"{stages}. This guard cannot tell which one runs `pip install -r "
            f"requirements.txt`, so it fails closed rather than checking the first and "
            f"reporting confidence it does not have. If this file became a multi-stage "
            f"build deliberately, teach the guard which stage installs the "
            f"requirements — do not relax it to the first match."
        )
        image_torch, image_cuda = stages[0]

        assert _normalize_version(image_torch) == _normalize_version(torch), (
            f"the PyTorch base image is built FROM pytorch/pytorch:{image_torch}-"
            f"cuda{image_cuda}, but gpu-requirementsHEADER.txt pins torch=={torch}. "
            f"pip would install the pinned wheel over the image's torch and leave "
            f"CUDA {image_cuda} underneath it, so the build succeeds and the failure "
            f"lands at GPU import or kernel launch instead. vllm and xformers carry "
            f"native extensions, so the pair has to agree. Move the FROM tag to a "
            f"torch {torch} image whose CUDA matches the nvidia-*-cu12 wheels the "
            f"requirement set resolves to."
        )


class TestTransformersIsPinnedWithinWhatVllmImportsWith:
    """``transformers`` must be pinned, and pinned below vllm's import-time ceiling.

    Leaving it unpinned is the defect, not a style choice. vllm declares no ceiling, so
    pip takes the newest transformers, and every version from 4.54.0 on breaks
    ``import vllm`` on a name collision that no resolver can see. An unpinned
    transitive dependency here means the GPU image's importability changes with
    whatever PyPI published most recently.
    """

    def test_transformers_is_pinned(self, base_pins):
        assert "transformers" in base_pins, (
            "requirements-base.txt must pin transformers. It is unpinned today, so "
            "pip resolves whatever is newest and vllm 0.9.0 fails to import against "
            "anything from 4.54.0 on — a break that appears with no change to this "
            "repository at all. See VLLM_TRANSFORMERS_SPEC."
        )

    def test_transformers_is_within_vllms_import_range(self, gpu_pins, base_pins):
        vllm = gpu_pins.get("vllm")
        transformers = base_pins.get("transformers")
        if vllm is None:
            pytest.skip("this guard only applies while the GPU header pins vllm")
        assert transformers is not None, "requirements-base.txt must pin transformers"

        specifier = _measured(VLLM_TRANSFORMERS_SPEC, vllm)
        if specifier is _MISSING:
            pytest.fail(
                f"vllm {vllm} is not in VLLM_TRANSFORMERS_SPEC. This range cannot be "
                f"read off PyPI metadata — vllm declares no ceiling. Import vllm "
                f"inside a built GPU image against candidate transformers versions, "
                f"record what actually works with today's date, then re-run."
            )

        assert _satisfies(transformers, specifier), (
            f"vllm {vllm} imports only with transformers{specifier}, but "
            f"requirements-base.txt pins transformers=={transformers}. The image will "
            f"BUILD and then fail at ``import vllm`` with \"'aimv2' is already used by "
            f'a Transformers config". Resolution cannot catch this; only an import in '
            f"a built image can."
        )


class TestSympySatisfiesTheTorchPin:
    """``torch`` constrains ``sympy``, and the two live in different files.

    The GPU header pins torch; the shared base pins sympy. Neither file shows the
    other, so this is the coupling the first attempt at #472 missed — the vllm and
    xformers guards were green while the set still failed to resolve.
    """

    @pytest.mark.parametrize("header_name", ["cpu", "gpu"])
    def test_sympy_meets_the_floor_that_the_pinned_torch_requires(
        self, header_name, cpu_pins, gpu_pins, base_pins
    ):
        pins = cpu_pins if header_name == "cpu" else gpu_pins
        torch = pins.get("torch")
        sympy = base_pins.get("sympy")
        if torch is None or sympy is None:
            pytest.skip(
                "this guard only applies while the header pins torch and the shared "
                "base pins sympy"
            )

        specifier = _measured(TORCH_SYMPY_SPEC, torch)
        if specifier is _MISSING:
            pytest.fail(
                f"torch {torch} is not in TORCH_SYMPY_SPEC. Read its "
                f"``requires_dist`` on PyPI, add the row with today's date, then "
                f"re-run. Do not delete this guard to get past it: torch pinned sympy "
                f"exactly at 2.6.0 and moved to a floor at 2.7.0, so an unverified "
                f"pair is a base-image build failure (#472)."
            )

        assert _satisfies(sympy, specifier), (
            f"{header_name}-requirementsHEADER.txt pins torch=={torch}, which requires "
            f"sympy{specifier}, but requirements-base.txt pins "
            f"sympy=={sympy}. The base-image requirement set cannot resolve. Note "
            f"torch 2.6.0 pins sympy EXACTLY, so on a torch downgrade a higher sympy "
            f"is as wrong as a lower one. sympy is in the SHARED base while torch is "
            f"in the headers, so neither file shows this on its own — move them "
            f"together."
        )


class TestSatisfiesModelsTheWholeDeclaredRange:
    """The comparison helper must honour every clause a release declares.

    Three holes in the first version of this module, all found by review on
    2026-09-15 and all the same mistake: a one-sided comparison standing in for a
    declared range. Each case below is green under the correct helper and was green
    under the broken guards too — which is the point, since the broken guards were
    green while the pair could not resolve.
    """

    @pytest.mark.parametrize(
        "candidate, specifier, expected, why",
        [
            (
                "1.26.0",
                ">=1.26.0,<1.27.0",
                True,
                "the lower bound is inclusive",
            ),
            (
                "1.44.0",
                ">=1.26.0,<1.27.0",
                False,
                "vllm 0.8.5's ceiling excludes 1.44.0 — the original #472 conflict",
            ),
            (
                "1.25.0",
                ">=1.26.0",
                False,
                "a floor-only range still has a floor: vllm 0.9.0 needs >=1.26.0, so "
                "an SDK downgrade must not pass",
            ),
            (
                "1.26.0",
                ">=1.27.0",
                False,
                "vllm 0.19.0 raised the floor to 1.27.0, so constraints are not "
                "monotonic across releases",
            ),
            (
                "1.13.3",
                "==1.13.1",
                False,
                "torch 2.6.0 pins sympy exactly, so a HIGHER sympy is still wrong",
            ),
            (
                "1.13.1",
                "==1.13.1",
                True,
                "the exact pin is satisfied only by itself",
            ),
        ],
    )
    def test_every_clause_is_enforced(self, candidate, specifier, expected, why):
        assert _satisfies(candidate, specifier) is expected, why


class TestVersionComparisonFollowsPep440:
    """Ordered bounds must follow PEP 440, not a truncated numeric tuple.

    Adversarial review on 2026-09-16 found the hand-rolled comparison wrong in both
    directions. ``_release`` kept only leading numeric components, so it stopped at the
    first non-numeric one: ``1.27.0rc1`` compared as ``(1, 27)``, and so did the
    abbreviated ``1.27``. Against ``<1.27.0`` — vllm 0.8.5's ceiling, the exact
    constraint behind #472 — ``(1, 27) < (1, 27, 0)`` held, so both passed.

    Both are wrong. PEP 440 zero-pads, so ``1.27`` IS ``1.27.0`` and the ceiling
    excludes it; and an exclusive ordered bound excludes a pre-release of its own
    version, so ``<1.27.0`` excludes ``1.27.0rc1`` too. The truncation also produced
    false REDS: ``1.26.1rc1`` and ``1.26`` both failed ``>=1.26.0``, which PEP 440
    accepts.

    Hand-rolling PEP 440 was the mistake. ``packaging`` decides this now — it is an
    unconditional pytest dependency (``packaging>=22``), so it is present wherever this
    suite runs, and it adds nothing to either image.

    ``prereleases=True`` is deliberate: every version reaching these helpers comes from
    an explicit ``==`` pin in a requirement file, which is exactly the case where pip
    also considers a pre-release.
    """

    @pytest.mark.parametrize(
        "candidate, specifier, expected, why",
        [
            (
                "1.27.0rc1",
                ">=1.26.0,<1.27.0",
                False,
                "an exclusive ordered bound excludes a pre-release of its own "
                "version, so vllm 0.8.5's ceiling rejects 1.27.0rc1. The truncating "
                "comparison passed it",
            ),
            (
                "1.27",
                ">=1.26.0,<1.27.0",
                False,
                "PEP 440 zero-pads, so 1.27 is 1.27.0 and the ceiling excludes it. "
                "The truncating comparison passed it",
            ),
            (
                "1.26.1rc1",
                ">=1.26.0",
                True,
                "1.26.1rc1 is above the 1.26.0 floor. The truncating comparison "
                "rejected it — a false red on a pin that resolves",
            ),
            (
                "1.26",
                ">=1.26.0",
                True,
                "1.26 is 1.26.0, which meets its own floor. Also a false red before",
            ),
            (
                "2.7.0.post1",
                "==2.7.0",
                False,
                "a post release is a different distribution, so an exact pin rejects "
                "it — the finding-6 behaviour, now enforced by packaging",
            ),
            (
                "2.7.0",
                "==2.7.0",
                True,
                "the exact pin is satisfied by itself",
            ),
            (
                "1.44.0",
                ">=1.26.0,<1.27.0",
                False,
                "the original #472 conflict: the shared base's SDK against vllm "
                "0.8.5's ceiling",
            ),
        ],
    )
    def test_the_comparison_matches_pep_440(self, candidate, specifier, expected, why):
        assert _satisfies(candidate, specifier) is expected, why

    @pytest.mark.parametrize(
        "label, table",
        [
            ("VLLM_OTEL_SPEC", "vllm_otel"),
            ("XFORMERS_TORCH_SPEC", "xformers_torch"),
            ("VLLM_TRANSFORMERS_SPEC", "vllm_transformers"),
            ("TORCH_SYMPY_SPEC", "torch_sympy"),
            ("VLLM_TORCH_SPEC", "vllm_torch"),
        ],
    )
    def test_every_recorded_specifier_parses(self, label, table):
        tables = {
            "vllm_otel": VLLM_OTEL_SPEC,
            "xformers_torch": XFORMERS_TORCH_SPEC,
            "vllm_transformers": VLLM_TRANSFORMERS_SPEC,
            "torch_sympy": TORCH_SYMPY_SPEC,
            "vllm_torch": VLLM_TORCH_SPEC,
        }
        for key, specifier in tables[table].items():
            try:
                Version(key)
            except InvalidVersion:
                pytest.fail(f"{label} key {key!r} is not a PEP 440 version")
            if specifier is None:
                continue
            try:
                SpecifierSet(specifier)
            except InvalidSpecifier:
                pytest.fail(
                    f"{label}[{key!r}] holds {specifier!r}, which is not a PEP 440 "
                    f"specifier. Copy it verbatim from the package's requires_dist."
                )

    def test_an_unsupported_operator_is_rejected_rather_than_ignored(self):
        with pytest.raises(InvalidSpecifier):
            _satisfies("1.0.0", "=>1.0.0")


class TestMeasuredTablesKeyOnTheFullVersion:
    """A table key must identify ONE distribution, post identifier included.

    Review on 2026-09-16 found the table keys were numeric release tuples, so
    ``_release`` mapped ``0.0.29``, ``0.0.29.post1``, ``0.0.29.post2`` and
    ``0.0.29.post3`` all onto ``(0, 0, 29)`` — four distributions, one row. The row
    then held whichever constraint was measured last, and the module reported it for
    the other three.

    That was not hypothetical: the row read ``torch==2.6.0``, which is the post2 and
    post3 constraint, while xformers 0.0.29 and 0.0.29.post1 both require
    ``torch==2.5.1``. So the guard approved xformers 0.0.29 with torch 2.6.0 — a pair
    pip rejects — and rejected xformers 0.0.29 with torch 2.5.1, which is the pair
    upstream actually built. Measured from PyPI ``requires_dist`` on 2026-09-16.
    """

    @pytest.mark.parametrize(
        "xformers_version, torch_version",
        [
            ("0.0.29", "2.5.1"),
            ("0.0.29.post1", "2.5.1"),
            ("0.0.29.post2", "2.6.0"),
            ("0.0.29.post3", "2.6.0"),
            ("0.0.30", "2.7.0"),
            ("0.0.31", "2.7.1"),
            ("0.0.31.post1", "2.7.1"),
            ("0.0.32.post1", "2.8.0"),
            ("0.0.32.post2", "2.8.0"),
            ("0.0.33", "2.9.0"),
            ("0.0.33.post1", "2.9.0"),
            ("0.0.33.post2", "2.9.1"),
        ],
    )
    def test_each_measured_xformers_release_accepts_its_own_torch(
        self, xformers_version, torch_version
    ):
        specifier = _measured(XFORMERS_TORCH_SPEC, xformers_version)
        assert specifier is not _MISSING, (
            f"xformers {xformers_version} is measured in this test but missing from "
            f"XFORMERS_TORCH_SPEC."
        )
        assert _satisfies(torch_version, specifier), (
            f"xformers {xformers_version} requires torch=={torch_version} as "
            f"measured, but the table says torch{specifier}."
        )

    @pytest.mark.parametrize(
        "left, right, why",
        [
            (
                "0.0.29",
                "0.0.29.post2",
                "xformers rebuilt 0.0.29 against a newer torch under a post tag, so "
                "collapsing the two reuses the wrong constraint",
            ),
            (
                "0.0.33",
                "0.0.33.post2",
                "the same split happened again at 0.0.33: 2.9.0 versus 2.9.1",
            ),
        ],
    )
    def test_a_post_release_is_not_read_as_its_base_release(self, left, right, why):
        assert _measured(XFORMERS_TORCH_SPEC, left) != _measured(
            XFORMERS_TORCH_SPEC, right
        ), why

    @pytest.mark.parametrize(
        "candidate, bound, expected, why",
        [
            ("2.7.0", "2.7.0", True, "the exact pin is satisfied by itself"),
            (
                "2.7.0.post1",
                "2.7.0",
                False,
                "a post release is a DIFFERENT distribution, so an exact clause must "
                "reject it rather than truncating it to the release segment",
            ),
            (
                "1.13.1",
                "1.13.1",
                True,
                "torch 2.6.0's exact sympy pin still matches itself",
            ),
        ],
    )
    def test_an_exact_clause_compares_the_whole_version(
        self, candidate, bound, expected, why
    ):
        assert _satisfies(candidate, f"=={bound}") is expected, why

    @pytest.mark.parametrize(
        "label, table",
        [
            ("VLLM_OTEL_SPEC", "vllm_otel"),
            ("XFORMERS_TORCH_SPEC", "xformers_torch"),
            ("VLLM_TRANSFORMERS_SPEC", "vllm_transformers"),
            ("TORCH_SYMPY_SPEC", "torch_sympy"),
            ("VLLM_TORCH_SPEC", "vllm_torch"),
        ],
    )
    def test_every_table_is_keyed_by_version_strings(self, label, table):
        tables = {
            "vllm_otel": VLLM_OTEL_SPEC,
            "xformers_torch": XFORMERS_TORCH_SPEC,
            "vllm_transformers": VLLM_TRANSFORMERS_SPEC,
            "torch_sympy": TORCH_SYMPY_SPEC,
            "vllm_torch": VLLM_TORCH_SPEC,
        }
        offenders = [key for key in tables[table] if not isinstance(key, str)]
        assert not offenders, (
            f"{label} is keyed by {offenders}, not by version strings. A numeric "
            f"tuple key drops the post identifier and merges distinct distributions "
            f"into one row."
        )


class TestProtectedPackagesCarryAnExactPin:
    """A protected package written as a RANGE must fail, not read as absent.

    Every guard above skips when its subject is missing from the file, and for a
    genuinely absent package that is right — the CPU header carries no vllm. But
    ``_parse_pins`` reads only ``==``, so ``vllm>=0.9.0`` is indistinguishable from
    no vllm at all and the skip turns the guard OFF rather than tightening it.

    Found by review on 2026-09-16. A range on any of vllm, opentelemetry-sdk, sympy
    or xformers silently disabled that package's guard while pip stayed free to
    resolve an unmeasured release — the same green-guard-failing-build shape as #472
    itself. An extras marker did it too: ``_PIN_PATTERN`` did not allow ``[...]``, so
    ``torch[opt]==2.7.0`` also read as absent.
    """

    @pytest.mark.parametrize(
        "line, expected",
        [
            ("vllm>=0.9.0", {"vllm": ">=0.9.0"}),
            ("opentelemetry-sdk>=1.26.0", {"opentelemetry-sdk": ">=1.26.0"}),
            ("sympy>1.13.0,<2", {"sympy": ">1.13.0,<2"}),
            ("xformers", {"xformers": ""}),
            ("vllm==0.9.0", {}),
            ("torch[opt]==2.7.0", {}),
            ("langgraph-prebuilt<1.0.9", {}),
            ("# vllm>=0.9.0", {}),
        ],
    )
    def test_a_range_on_a_protected_package_is_not_read_as_absent(self, line, expected):
        assert _unpinned_protected(line) == expected

    @pytest.mark.parametrize(
        "label, path",
        [
            ("cpu-requirementsHEADER.txt", CPU_HEADER),
            ("gpu-requirementsHEADER.txt", GPU_HEADER),
            ("requirements-base.txt", BASE_REQUIREMENTS),
        ],
    )
    def test_every_protected_package_present_is_exactly_pinned(self, label, path):
        unpinned = _unpinned_protected(path.read_text(encoding="utf-8"))
        assert not unpinned, (
            f"{label} names {sorted(unpinned)} without an exact ``==`` pin "
            f"({unpinned}). Every guard in this module skips when its subject is "
            f"absent, and a range is read as absent, so this would disable the "
            f"guard instead of relaxing it. Pin the package exactly and add the "
            f"measured row, or drop it from PROTECTED_PACKAGES with a reason."
        )


class TestNoRequirementFileDefersToAnotherFile:
    """An ``-r``/``-c`` include would make a protected package invisible here.

    Adversarial review on 2026-09-16: ``_requirement_lines`` drops every line starting
    with ``-``, which is right for ``--extra-index-url`` (the CPU header has two) but
    wrong for ``-r other.txt`` and ``-c constraints.txt``. Those pull in real
    requirements that pip installs and this module cannot see, so a protected package
    supplied through an include reads as absent and turns its guard off — the same
    silent skip as a range, by a different route.

    Resolving includes recursively is the other possible answer. Refusing them is
    better here: the three files are assembled by ``cat`` in
    ``build_docker_images.sh``, so an include has no reason to appear, and a guard that
    fails closed on an unsupported construct beats one that half-supports it. No file
    uses an include today, so this costs nothing until somebody adds one.
    """

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("-r other.txt\n", ["-r other.txt"]),
            ("-c constraints.txt\n", ["-c constraints.txt"]),
            ("--requirement other.txt\n", ["--requirement other.txt"]),
            ("--constraint constraints.txt\n", ["--constraint constraints.txt"]),
            ("--extra-index-url https://example.invalid/simple\n", []),
            ("--index-url https://example.invalid/simple\n", []),
            ("torch==2.7.0\n", []),
            ("# -r other.txt\n", []),
        ],
    )
    def test_an_include_directive_is_reported(self, text, expected):
        assert _include_directives(text) == expected

    @pytest.mark.parametrize(
        "label, path",
        [
            ("cpu-requirementsHEADER.txt", CPU_HEADER),
            ("gpu-requirementsHEADER.txt", GPU_HEADER),
            ("requirements-base.txt", BASE_REQUIREMENTS),
            ("base-python-image/requirements.txt", GENERATED_CPU_REQUIREMENTS),
            ("base-pytorch-image/requirements.txt", GENERATED_GPU_REQUIREMENTS),
        ],
    )
    def test_no_file_pulls_requirements_in_from_elsewhere(self, label, path):
        includes = _include_directives(path.read_text(encoding="utf-8"))
        assert not includes, (
            f"{label} uses {includes}. Every guard in this module reads only the file "
            f"in front of it, so a package supplied through an include is invisible "
            f"here and its guard silently skips, while pip installs it anyway. These "
            f"sets are assembled by ``cat``, so inline the pins instead — or teach "
            f"_requirement_lines to resolve includes before relaxing this."
        )


class TestProtectedPinsAreUnconditional:
    """A protected pin carrying an environment marker must fail closed.

    Found by the async reviewer on 2026-09-16, on the parser this round introduced.
    ``_requirement_lines`` cuts a line at ``;`` to drop the marker, so
    ``transformers==4.53.3; sys_platform == "win32"`` reads here as an unconditional
    exact pin: ``_parse_pins`` records 4.53.3 and ``_unpinned_protected`` reports
    healthy. On the Linux image pip ignores that line completely, leaving transformers
    unpinned and free to resolve 4.54+ — which is precisely the ``aimv2`` import break
    this guard exists to prevent.

    Evaluating markers against the image environment is the other option. Failing
    closed is the better answer for these files: they are ``cat``-concatenated into one
    Linux image each, a conditional pin on a protected package has no purpose here, and
    a guard that refuses a construct it cannot model beats one that quietly mismodels
    it. Markers on other packages are untouched. No file uses one today.
    """

    @pytest.mark.parametrize(
        "text, expected",
        [
            (
                'transformers==4.53.3; sys_platform == "win32"',
                {"transformers": 'sys_platform == "win32"'},
            ),
            (
                'torch==2.7.0 ; python_version >= "3.9"',
                {"torch": 'python_version >= "3.9"'},
            ),
            ("transformers==4.53.3", {}),
            ('markitdown[pdf,pptx]==0.1.5; sys_platform == "linux"', {}),
            ('# transformers==4.53.3; sys_platform == "win32"', {}),
        ],
    )
    def test_a_marker_on_a_protected_pin_is_reported(self, text, expected):
        assert _conditional_protected(text) == expected

    @pytest.mark.parametrize(
        "label, path",
        [
            ("cpu-requirementsHEADER.txt", CPU_HEADER),
            ("gpu-requirementsHEADER.txt", GPU_HEADER),
            ("requirements-base.txt", BASE_REQUIREMENTS),
            ("base-python-image/requirements.txt", GENERATED_CPU_REQUIREMENTS),
            ("base-pytorch-image/requirements.txt", GENERATED_GPU_REQUIREMENTS),
        ],
    )
    def test_no_protected_pin_is_conditional(self, label, path):
        conditional = _conditional_protected(path.read_text(encoding="utf-8"))
        assert not conditional, (
            f"{label} declares {sorted(conditional)} with an environment marker "
            f"({conditional}). The marker is stripped before these guards read the "
            f"pin, so a line pip skips on this image still reads here as an "
            f"unconditional pin — the guard passes while the package is effectively "
            f"unpinned. Drop the marker, or evaluate markers against the image "
            f"environment before relaxing this."
        )


class TestProtectedPackagesAreDeclaredOnce:
    """A protected package must be declared once, in one place.

    Adversarial review on 2026-09-16 found that both parsers keep only the last
    occurrence of a name, so a repeated declaration hides a contradiction instead of
    reporting it. ``vllm==0.9.0`` followed by ``vllm<0.9.0`` leaves an exact pin in
    ``_parse_pins``, ``_unpinned_protected`` reports healthy, and every pairwise guard
    passes — while pip correctly calls the set impossible.

    The concatenation is why this is not an exotic case. ``build_docker_images.sh``
    builds each image's set by joining a header with ``requirements-base.txt``, so a
    package pinned in BOTH files produces two lines in one file with nothing in this
    module comparing them: ``gpu_pins`` and ``base_pins`` are separate dicts. torch is
    pinned in the headers and sympy and transformers in the shared base today, and
    nothing stops the next edit from adding torch to the base.

    So the duplicate check runs over the GENERATED sets as well — the only files pip
    actually reads, and the only place a cross-file duplicate can be seen at all.
    """

    @pytest.mark.parametrize(
        "text, expected",
        [
            (
                "vllm==0.9.0\nvllm<0.9.0\n",
                {"vllm": ["==0.9.0", "<0.9.0"]},
            ),
            (
                "torch==2.7.0\ntorch==2.6.0\n",
                {"torch": ["==2.7.0", "==2.6.0"]},
            ),
            (
                "vllm==0.9.0\nVLLM==0.9.0\n",
                {"vllm": ["==0.9.0", "==0.9.0"]},
            ),
            ("vllm==0.9.0\n", {}),
            ("numpy==1.0\nnumpy==2.0\n", {}),
        ],
    )
    def test_a_repeated_protected_declaration_is_reported(self, text, expected):
        assert _duplicated_protected(text) == expected

    @pytest.mark.parametrize(
        "label, path",
        [
            ("cpu-requirementsHEADER.txt", CPU_HEADER),
            ("gpu-requirementsHEADER.txt", GPU_HEADER),
            ("requirements-base.txt", BASE_REQUIREMENTS),
            ("base-python-image/requirements.txt", GENERATED_CPU_REQUIREMENTS),
            ("base-pytorch-image/requirements.txt", GENERATED_GPU_REQUIREMENTS),
        ],
    )
    def test_each_protected_package_is_declared_once(self, label, path):
        duplicated = _duplicated_protected(path.read_text(encoding="utf-8"))
        assert not duplicated, (
            f"{label} declares {sorted(duplicated)} more than once ({duplicated}). "
            f"Both parsers keep the last occurrence, so the earlier one is invisible "
            f"to every guard while pip still sees both and can call the set "
            f"impossible. Declare the package in exactly one file."
        )

    @pytest.mark.parametrize(
        "label, header, generated",
        [
            ("base-python-image", CPU_HEADER, GENERATED_CPU_REQUIREMENTS),
            ("base-pytorch-image", GPU_HEADER, GENERATED_GPU_REQUIREMENTS),
        ],
    )
    def test_the_generated_set_matches_its_sources(self, label, header, generated):
        expected = header.read_text(encoding="utf-8") + BASE_REQUIREMENTS.read_text(
            encoding="utf-8"
        )
        assert generated.read_text(encoding="utf-8") == expected, (
            f"{label}/requirements.txt is not "
            f"``cat {header.name} requirements-base.txt``. That generated file is "
            f"what the image installs and what a ``pip install --dry-run`` resolves, "
            f"while every other guard here reads the sources — so a stale generated "
            f"file means this module is green about a requirement set the build does "
            f"not use. Re-run scripts/dev/build_docker_images.sh, or regenerate the "
            f"two files with that same concatenation."
        )


class TestParsingFailsClosedOnRequirementBearingOptions:
    """Adversarial review, 2026-09-16: a shape the parser cannot read was treated as
    a shape that is not there. That is backwards for a guard.
    """

    def test_an_editable_directive_is_reported_as_unreadable(self):
        """``-e``/``--editable`` installs a real project, so it must not vanish.

        ``_requirement_lines`` drops every line starting with ``-``, and the include
        matcher recognised only ``-r``/``-c``. So ``-e git+...#egg=vllm`` was invisible:
        every protected-dependency guard saw vllm as absent and SKIPPED, which would let
        an arbitrary unmeasured checkout into the GPU image.
        """
        text = "\n".join(
            [
                "torch==2.7.0",
                "-e git+https://github.com/vllm-project/vllm@main#egg=vllm",
                "--editable ./local/vllm",
            ]
        )
        reported = _requirement_bearing_directives(text)
        assert len(reported) == 2, (
            f"both editable directives must be reported as unreadable, got {reported}. "
            f"An editable install supplies a package no guard here can version-check, "
            f"so it must fail the suite rather than read as absent."
        )

    def test_include_directives_are_still_reported(self):
        text = "-r other.txt\n--constraint pins.txt\ntorch==2.7.0"
        assert len(_requirement_bearing_directives(text)) == 2

    def test_plain_option_lines_stay_inert(self):
        text = (
            "--extra-index-url https://example.invalid/simple\n"
            "--prefer-binary\n"
            "torch==2.7.0"
        )
        assert _requirement_bearing_directives(text) == []


class TestPytorchBaseStagesAreUnambiguous:
    """A multi-stage Dockerfile can name more than one pytorch base.

    ``re.search`` returned the FIRST match only, so a builder stage on the right torch
    could mask a later stage on the wrong one while ``pip install -r requirements.txt``
    ran against the mismatched base.
    """

    def test_every_pytorch_stage_is_collected(self):
        content = "\n".join(
            [
                "FROM docker.io/pytorch/pytorch:2.7.0-cuda12.6-cudnn9-devel AS builder",
                "RUN echo build",
                "FROM docker.io/pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel",
                "RUN pip install -r requirements.txt",
            ]
        )
        stages = _pytorch_from_stages(content)
        assert stages == [("2.7.0", "12.6"), ("2.6.0", "12.4")], (
            f"both stages must be collected, got {stages}. Taking only the first lets "
            f"a correct builder stage hide a wrong runtime stage."
        )

    def test_a_single_stage_is_still_read(self):
        content = "FROM docker.io/pytorch/pytorch:2.7.0-cuda12.6-cudnn9-devel\nRUN true"
        assert _pytorch_from_stages(content) == [("2.7.0", "12.6")]


class TestOpaqueRequirementsFailClosed:
    """A requirement with no readable project name must fail, not resolve to nonsense.

    Review on 2026-09-16: ``git+https://host/repo.git#egg=vllm`` had its ``#egg=vllm``
    fragment stripped as a comment, and the name matcher then recorded the project as
    ``git``. Every vllm guard read vllm as ABSENT and skipped while pip installed an
    arbitrary checkout. These lines do not begin with ``-``, so the directive matcher
    never saw them either.
    """

    @pytest.mark.parametrize(
        "line",
        [
            "git+https://github.com/vllm-project/vllm@main#egg=vllm",
            "hg+https://host/repo#egg=vllm",
            "https://host/vllm-0.9.0-py3-none-any.whl",
            "file:///opt/wheels/vllm-0.9.0.whl",
        ],
    )
    def test_a_vcs_or_url_requirement_is_reported(self, line):
        assert _opaque_requirements(f"torch==2.7.0\n{line}\n"), (
            f"{line!r} supplies a package with no version this module can check, so it "
            f"must be reported rather than parsed into a bogus project name."
        )

    def test_a_plain_pin_is_not_reported(self):
        assert _opaque_requirements("torch==2.7.0\nvllm==0.9.0\n") == []

    @pytest.mark.parametrize(
        "path",
        [GENERATED_CPU_REQUIREMENTS, GENERATED_GPU_REQUIREMENTS],
        ids=["cpu", "gpu"],
    )
    def test_the_generated_sets_carry_no_opaque_requirement(self, path):
        opaque = _opaque_requirements(path.read_text(encoding="utf-8"))
        assert opaque == [], (
            f"{path} declares {opaque}, whose project name this module cannot read. "
            f"Every compatibility guard would silently skip the package it supplies."
        )


class TestCompactOptionFormsAreRecognized:
    """pip accepts ``-rother.txt`` with no separator, and so must the matcher.

    Review on 2026-09-16: the pattern required whitespace, ``=`` or end-of-line after
    the short option, so the compact spelling matched nothing — while
    ``_requirement_lines`` still dropped the line for starting with ``-``, leaving the
    included file's pins invisible to every guard.
    """

    @pytest.mark.parametrize(
        "line",
        ["-rother.txt", "-cconstraints.txt", "-e./local/vllm", "-r other.txt"],
    )
    def test_a_compact_directive_is_reported(self, line):
        assert _requirement_bearing_directives(line) == [line]

    @pytest.mark.parametrize(
        "line",
        ["--extra-index-url https://example.invalid/simple", "--prefer-binary", "-v"],
    )
    def test_an_inert_option_is_not_reported(self, line):
        assert _requirement_bearing_directives(line) == []
