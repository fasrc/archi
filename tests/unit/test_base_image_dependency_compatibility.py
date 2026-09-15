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
``sympy>=1.13.3`` and the shared base pinned ``1.13.1``. Every constraint in this
module was added *after* a resolver found it.

So this module is a regression net, not a gate. The gate is resolving both generated
files, which catches transitive conflicts nobody predicted and costs seconds:

    pip install --dry-run -r src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt
    pip install --dry-run -r src/cli/templates/dockerfiles/base-python-image/requirements.txt

Neither the net nor the resolve proves the packages *import*. ``requirements-base.txt``
records a clean resolution whose every instrumentor then failed on ``pkg_resources``.
Only a real image build proves that, which is what #473 asks CI to do.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

CPU_HEADER = REPO_ROOT / "requirements" / "cpu-requirementsHEADER.txt"
GPU_HEADER = REPO_ROOT / "requirements" / "gpu-requirementsHEADER.txt"
BASE_REQUIREMENTS = REPO_ROOT / "requirements" / "requirements-base.txt"
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

# ``vllm`` capped the OpenTelemetry SDK from below its own floor until 0.9.0 dropped
# the upper bound. Measured from PyPI ``requires_dist`` on 2026-09-15:
#
#   vllm 0.8.5  -> opentelemetry-sdk<1.27.0,>=1.26.0   <- the cap
#   vllm 0.9.0  -> opentelemetry-sdk>=1.26.0           <- no upper bound
#   vllm 0.19.0 -> opentelemetry-sdk>=1.27.0
#
# The trap is that the only range vllm 0.8.5 accepts is itself uninstallable: that
# suite's ``opentelemetry-instrumentation`` imports ``pkg_resources`` at module
# scope, which setuptools 82 removed (see requirements-base.txt). So no
# ``opentelemetry-sdk`` pin satisfies both vllm 0.8.5 and a working exporter, and
# the fix has to be the vllm bump.
VLLM_OTEL_CAP_LIFTED_AT = (0, 9, 0)
VLLM_OTEL_CAP_CEILING = (1, 27, 0)

# ``xformers`` pins ``torch`` to one exact version per release, so a torch bump that
# leaves xformers behind produces a conflict rather than a fallback. Measured from
# PyPI ``requires_dist`` on 2026-09-15.
XFORMERS_TORCH = {
    (0, 0, 29): (2, 6, 0),
    (0, 0, 30): (2, 7, 0),
    (0, 0, 31): (2, 7, 1),
    (0, 0, 33): (2, 9, 0),
}

# ``torch`` constrains ``sympy``, which the SHARED base pins — so the coupling crosses
# the header/base boundary and is invisible in either file alone. This is the conflict
# the first pass at #472 missed: bumping torch to 2.7.0 left ``sympy==1.13.1`` in place
# and the set still would not resolve. Measured from PyPI ``requires_dist`` on
# 2026-09-15, as an inclusive floor per torch release:
#
#   torch 2.6.0 -> sympy==1.13.1 (exact, which is where the old pin came from)
#   torch 2.7.0 -> sympy>=1.13.3
TORCH_SYMPY_FLOOR = {
    (2, 6, 0): (1, 13, 1),
    (2, 7, 0): (1, 13, 3),
}

_PIN_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;#]+)")


def _release(version: str) -> tuple:
    """Leading numeric components of ``version``, as ints.

    ``0.0.29.post2`` yields ``(0, 0, 29)``. Comparison here only ever needs the
    release segment, so trailing ``.postN``/``rcN`` parts are dropped rather than
    ordered — this module never has to distinguish two builds of one release.
    """
    parts = []
    for component in version.split("."):
        if not component.isdigit():
            break
        parts.append(int(component))
    return tuple(parts)


def _pins(path: Path) -> dict:
    """Map normalized project name to pinned version for every ``name==version``.

    Comments, blanks and option lines such as ``--extra-index-url`` carry no pin and
    are skipped. Only ``==`` pins are read: this module asserts against exact pins,
    and a range would make every constraint below ambiguous.
    """
    pins = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = _PIN_PATTERN.match(line)
        if match:
            name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
            pins[name] = match.group(2)
    return pins


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

        if _release(sdk) < VLLM_OTEL_CAP_CEILING:
            pytest.skip(
                f"opentelemetry-sdk {sdk} is below {_fmt(VLLM_OTEL_CAP_CEILING)}, "
                "so no vllm release caps it"
            )

        assert _release(vllm) >= VLLM_OTEL_CAP_LIFTED_AT, (
            f"vllm {vllm} requires opentelemetry-sdk<{_fmt(VLLM_OTEL_CAP_CEILING)}, "
            f"but requirements-base.txt pins opentelemetry-sdk=={sdk}. pip cannot "
            f"resolve the PyTorch image's requirement set, so the release build "
            f"fails at the build step (#472). vllm {_fmt(VLLM_OTEL_CAP_LIFTED_AT)} "
            f"is the first release that drops the upper bound. Bumping the SDK down "
            f"instead does NOT work: that range is uninstallable on current "
            f"setuptools, as requirements-base.txt records."
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

        expected_torch = XFORMERS_TORCH.get(_release(xformers))
        if expected_torch is None:
            pytest.fail(
                f"xformers {xformers} is not in XFORMERS_TORCH. Read its "
                f"``requires_dist`` on PyPI, add the row with today's date, then "
                f"re-run. Do not delete this guard to get past it: xformers pins "
                f"torch exactly, so an unverified pair is a build failure waiting "
                f"for the next release (#472)."
            )

        assert _release(torch) == expected_torch, (
            f"xformers {xformers} requires torch=={_fmt(expected_torch)}, but the "
            f"GPU header pins torch=={torch}. xformers ships one build per torch "
            f"release, so this set cannot resolve."
        )


class TestPytorchBaseImageMatchesTheTorchPin:
    """The ``FROM`` image already ships a torch, and ``pip`` then installs the pin.

    When the two disagree, ``pip`` replaces the base image's torch with a PyPI wheel
    while the image keeps the older CUDA and cuDNN system libraries underneath. The
    build still succeeds, so nothing fails until a GPU import or a kernel launch —
    and ``vllm`` and ``xformers`` both carry native extensions compiled against one
    specific torch and CUDA pair.

    This guard needs no measured table and no network: both values live in this
    repository, so it cannot rot the way ``XFORMERS_TORCH`` and
    ``TORCH_SYMPY_FLOOR`` can. It compares only the torch version. Whether the
    image's CUDA matches the ``nvidia-*-cu12`` wheels the set resolves to is a
    resolver question, not a text question — see the module docstring.
    """

    def test_from_image_declares_the_pinned_torch(self, gpu_pins):
        torch = gpu_pins.get("torch")
        assert torch is not None, "gpu-requirementsHEADER.txt must pin torch"

        content = PYTORCH_BASE_DOCKERFILE.read_text(encoding="utf-8")
        match = _PYTORCH_FROM_PATTERN.search(content)
        assert match, (
            f"{PYTORCH_BASE_DOCKERFILE} has no recognizable "
            f"``FROM .../pytorch/pytorch:<torch>-cuda<ver>`` line. If the base image "
            f"moved to a different publisher, update _PYTORCH_FROM_PATTERN rather "
            f"than deleting this guard."
        )
        image_torch, image_cuda = match.group(1), match.group(2)

        assert _release(image_torch) == _release(torch), (
            f"the PyTorch base image is built FROM pytorch/pytorch:{image_torch}-"
            f"cuda{image_cuda}, but gpu-requirementsHEADER.txt pins torch=={torch}. "
            f"pip would install the pinned wheel over the image's torch and leave "
            f"CUDA {image_cuda} underneath it, so the build succeeds and the failure "
            f"lands at GPU import or kernel launch instead. vllm and xformers carry "
            f"native extensions, so the pair has to agree. Move the FROM tag to a "
            f"torch {torch} image whose CUDA matches the nvidia-*-cu12 wheels the "
            f"requirement set resolves to."
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

        floor = TORCH_SYMPY_FLOOR.get(_release(torch))
        if floor is None:
            pytest.fail(
                f"torch {torch} is not in TORCH_SYMPY_FLOOR. Read its "
                f"``requires_dist`` on PyPI, add the row with today's date, then "
                f"re-run. Do not delete this guard to get past it: torch pinned sympy "
                f"exactly at 2.6.0 and moved the floor at 2.7.0, so an unverified "
                f"pair is a base-image build failure (#472)."
            )

        assert _release(sympy) >= floor, (
            f"{header_name}-requirementsHEADER.txt pins torch=={torch}, which requires "
            f"sympy>={_fmt(floor)}, but requirements-base.txt pins sympy=={sympy}. The "
            f"base-image requirement set cannot resolve. sympy is in the SHARED base "
            f"while torch is in the headers, so neither file shows this on its own — "
            f"bump sympy whenever torch moves."
        )


def _fmt(release: tuple) -> str:
    return ".".join(str(part) for part in release)
