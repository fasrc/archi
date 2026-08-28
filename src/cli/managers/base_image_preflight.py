"""Establish that `archi create` can obtain its base images before it destroys anything.

The service templates build ``FROM`` an ``a2rchi-*-base`` image and then run ``pip install .``.
When that image carries an interpreter below the declared ``requires-python`` floor, every
service build fails -- and under ``--force`` it fails *after* ``remove_existing_deployment()``
has already removed the operator's working deployment. This module is what refuses first
(fasrc/archi#266, and the ordering contract from #287).

The governing invariant, which every function here serves:

    Every path either **establishes** that a base image is usable, **refuses**, or **says out
    loud that it could not tell**. No path may pass silently on an assumption.

The real-create half has no third option -- it materializes each image and reads its version.
Only a dry run, which must not pull, can end in "could not tell", and it names the reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Sequence

PYTHON_BASE = "a2rchi-python-base"
PYTORCH_BASE = "a2rchi-pytorch-base"

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "cli" / "templates" / "dockerfiles"

# Templates excluded from the service set. Each value is the reason the file is not a
# service template, so a reader can tell a base-defining template from a third-party-based
# one without opening it.
NON_SERVICE_TEMPLATES: dict[str, str] = {
    "Dockerfile-base": "defines the a2rchi-python-base image itself",
    "Dockerfile-base-gpu": "defines the a2rchi-pytorch-base image itself",
    "Dockerfile-postgres": "builds on docker.io/pgvector/pgvector:pg17",
    "Dockerfile-grafana": "builds on docker.io/grafana/grafana-enterprise:10.2.0",
}

# `FROM <ref>` where the reference names an a2rchi base image. `\S+` stops at whitespace, so
# the trailing spaces several templates carry on that line never reach the tag.
_FROM_BASE_RE = re.compile(r"^FROM\s+(?P<ref>\S*a2rchi-\w+-base\S*)", re.MULTILINE)

LOCAL_PREFIX = "localhost/"


class Cause(str, Enum):
    """Why a reference could not be established. Each maps to a different operator action."""

    UNAUTHORIZED = "unauthorized"
    UNKNOWN_TAG = "unknown_tag"
    UNREACHABLE = "unreachable"
    NO_DISK = "no_disk"
    LOCAL_BUILD_MISSING = "local_build_missing"
    NO_RUNTIME = "no_runtime"
    VERSION_BELOW_FLOOR = "version_below_floor"
    VERSION_UNREADABLE = "version_unreadable"
    PROBE_UNSUPPORTED = "probe_unsupported"
    NOT_PULLED = "not_pulled"


class Verdict(str, Enum):
    AVAILABLE = "available"
    REFUSED = "refused"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Outcome:
    """What the preflight concluded about one base image reference."""

    reference: str
    verdict: Verdict
    cause: Optional[Cause] = None
    detail: str = ""

    @property
    def refused(self) -> bool:
        return self.verdict is Verdict.REFUSED

    @property
    def unverified(self) -> bool:
        return self.verdict is Verdict.UNVERIFIED


def service_templates(template_dir: Optional[Path] = None) -> List[Path]:
    """The sorted Paths of every Dockerfile* that is a service template.

    Service templates build ``FROM`` an ``a2rchi-*-base`` image. The four excluded by
    ``NON_SERVICE_TEMPLATES`` define base images themselves or build on third-party images.
    """
    directory = template_dir or TEMPLATE_DIR
    return sorted(
        p for p in directory.glob("Dockerfile*") if p.name not in NON_SERVICE_TEMPLATES
    )


def stale_template_exclusions(template_dir: Optional[Path] = None) -> List[str]:
    """Exclusion names in ``NON_SERVICE_TEMPLATES`` that have no matching file on disk.

    A non-empty return means the exclusion list names a template that no longer exists,
    so it excludes nothing and silently over-reports the service-template count.
    """
    directory = template_dir or TEMPLATE_DIR
    return [name for name in NON_SERVICE_TEMPLATES if not (directory / name).exists()]


def templates_missing_base_reference(template_dir: Optional[Path] = None) -> List[Path]:
    """Service templates that carry no ``FROM`` referencing an ``a2rchi-*-base`` image.

    A non-empty return means a service template has either lost its base ``FROM`` line or
    replaced it with a third-party image.  The deploy preflight cannot cover these templates,
    so the caller should treat a non-empty list as a refusal to proceed.
    """
    return [
        template
        for template in service_templates(template_dir)
        if not _FROM_BASE_RE.search(template.read_text())
    ]


def base_reference(image: str, template_dir: Optional[Path] = None) -> Optional[str]:
    """The pinned reference the templates declare for ``image``.

    Read from the templates rather than composed from a constant, so the preflight cannot
    check a different tag than the one the build will use.
    """
    directory = template_dir or TEMPLATE_DIR
    for dockerfile in sorted(directory.glob("Dockerfile-*")):
        for match in _FROM_BASE_RE.finditer(dockerfile.read_text()):
            reference = match.group("ref")
            if image in reference:
                return reference
    return None


def required_base_images(
    gpu_ids: Optional[str],
    grader_enabled: bool,
    template_dir: Optional[Path] = None,
) -> List[str]:
    """The base image references this deployment needs, as pinned by the templates.

    The python base is always required: `config-seed` builds `Dockerfile-chat` whether or not
    the chatbot is enabled, so no supported configuration skips it.

    The pytorch base is required only when a GPU is requested or the grader is enabled.
    `Dockerfile-grader` is a non-GPU service on the pytorch base, which is why the grader is
    named here rather than inferred from the GPU flag. Deciding this by rule instead of by a
    service-to-template map is design D4; `test_two_image_rule_still_matches_every_template`
    is what keeps the rule and the templates from drifting apart.

    Raises ``BaseImagePreflightError`` when any service template carries no ``a2rchi-*-base``
    FROM reference. The preflight cannot cover such a template, and passing silently would
    violate the governing invariant: no path may pass silently on an assumption.
    """
    _refuse_uncoverable_templates(template_dir)

    references = []
    for image in required_base_image_names(gpu_ids, grader_enabled):
        reference = base_reference(image, template_dir)
        if reference:
            references.append(reference)
    return references


def required_base_image_names(
    gpu_ids: Optional[str], grader_enabled: bool
) -> List[str]:
    """The rule itself, with no filesystem in it: which base images this deployment needs."""
    images = [PYTHON_BASE]
    if gpu_ids or grader_enabled:
        images.append(PYTORCH_BASE)
    return images


def decide_availability(
    reference: str,
    *,
    runtime_available: bool,
    present_locally: bool,
    fetch_cause: Optional[Cause] = None,
    dry: bool = False,
) -> Outcome:
    """Decide one reference from probe results. Pure: never shells out, never raises.

    ``fetch_cause`` is ``None`` when the fetch succeeded -- a pull on a real create, a
    reachability check on a dry run -- and otherwise names why it did not.
    """
    if not runtime_available:
        # A dry run is allowed to proceed without a runtime; a real create is not, because
        # compose needs that same runtime minutes later (design D7).
        if dry:
            return Outcome(reference, Verdict.UNVERIFIED, Cause.NO_RUNTIME)
        return Outcome(reference, Verdict.REFUSED, Cause.NO_RUNTIME)

    if present_locally:
        return Outcome(reference, Verdict.AVAILABLE)

    if reference.startswith(LOCAL_PREFIX):
        # A `localhost/` reference is the tag `build_docker_images.sh` gives a locally built
        # base. It is a registry-style name, not evidence of presence, and no registry can
        # supply it -- so absent means absent.
        return Outcome(reference, Verdict.REFUSED, Cause.LOCAL_BUILD_MISSING)

    if fetch_cause is None:
        # Pulled on a real create; merely reachable on a dry run, which cannot read a version
        # it did not fetch.
        if dry:
            return Outcome(reference, Verdict.UNVERIFIED, Cause.NOT_PULLED)
        return Outcome(reference, Verdict.AVAILABLE)

    if fetch_cause is Cause.PROBE_UNSUPPORTED and dry:
        # The dry path alone: the real path pulls, and pull is universally supported. A dry
        # run destroys nothing, so an unusable probe is reported rather than fatal. Guarding
        # this on `dry` is load-bearing -- without it a real create could return UNVERIFIED,
        # which is the assumption-passing this module exists to remove.
        return Outcome(reference, Verdict.UNVERIFIED, Cause.PROBE_UNSUPPORTED)

    return Outcome(reference, Verdict.REFUSED, fetch_cause)


def check_python_floor(reference: str, reported: Optional[str], floor: str) -> Outcome:
    """Compare a present image's interpreter against the declared floor.

    An unreadable version refuses. Passing it would convert an unknown compatibility result
    into permission to tear down a working deployment, which is the failure this module
    exists to prevent. The probe runs a container and so does the build, so a host that
    cannot run the probe could not have completed the build either (design D5).
    """
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    version_text = _parse_version(reported)
    if version_text is None:
        return Outcome(
            reference,
            Verdict.REFUSED,
            Cause.VERSION_UNREADABLE,
            detail=(reported or "").strip(),
        )

    # `_parse_version` yields only digits and dots, which is always a valid PEP 440 version,
    # so there is no parse failure left to guard against here.
    version = Version(version_text)

    if not SpecifierSet(floor).contains(version):
        return Outcome(
            reference,
            Verdict.REFUSED,
            Cause.VERSION_BELOW_FLOOR,
            detail=f"{version} against {floor}",
        )
    return Outcome(reference, Verdict.AVAILABLE)


def _parse_version(reported: Optional[str]) -> Optional[str]:
    """The version out of ``python -V`` output, or ``None`` when it is not readable."""
    if not reported:
        return None
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", reported)
    return match.group(1) if match else None


def compose_message(outcome: Outcome, container_tool: str = "docker") -> str:
    """The operator-facing diagnostic for a refusal or an unverified result.

    Each cause carries its own remedy. One collapsed message would send an operator with a
    stale pin, or a full disk, to `docker login` -- which cannot fix either (design D3).
    """
    reference = outcome.reference
    registry = reference.split("/", 1)[0] if "/" in reference else reference

    if outcome.cause is Cause.UNAUTHORIZED:
        return (
            f"Not authorized to pull the base image {reference}.\n"
            f"  The fasrc packages are 'internal', so a login is required:\n"
            f"    echo $TOKEN | {container_tool} login {registry} -u <user> --password-stdin\n"
            f"  The token MUST be a classic personal access token carrying 'read:packages'. "
            f"A fine-grained token has no Packages permission and fails identically.\n"
            f"  If SSO is enforced, authorize the token for the organization first."
        )
    if outcome.cause is Cause.UNKNOWN_TAG:
        return (
            f"The base image {reference} does not exist in its registry.\n"
            f"  The pin is stale or the tag was deleted. Logging in will not help.\n"
            f"  Re-run scripts/dev/update_service_base_images.py to repin."
        )
    if outcome.cause is Cause.UNREACHABLE:
        return (
            f"Could not reach the registry for the base image {reference}.\n"
            f"  This is a network or registry fault; nothing in archi needs changing."
        )
    if outcome.cause is Cause.NO_DISK:
        return (
            f"Ran out of disk space fetching the base image {reference}.\n"
            f"  Free space and retry. This is not an authentication problem."
        )
    if outcome.cause is Cause.LOCAL_BUILD_MISSING:
        return (
            f"The base image {reference} is not present and cannot be pulled.\n"
            f"  A 'localhost/' reference names a locally built image, so build it first:\n"
            f"    scripts/dev/build_docker_images.sh <tag>"
        )
    if outcome.cause is Cause.NO_RUNTIME:
        return (
            f"No usable container runtime, so the base image {reference} cannot be "
            f"obtained.\n"
            f"  Install {container_tool}, or select the other container tool."
        )
    if outcome.cause is Cause.VERSION_BELOW_FLOOR:
        return (
            f"The base image {reference} carries a Python below this project's floor "
            f"({outcome.detail}).\n"
            f"  Every service build would fail at 'pip install .'."
        )
    if outcome.cause is Cause.VERSION_UNREADABLE:
        detail = f" (got {outcome.detail!r})" if outcome.detail else ""
        return (
            f"Could not determine the Python version of the base image {reference}"
            f"{detail}.\n"
            f"  Refusing rather than assuming it is compatible."
        )
    if outcome.cause is Cause.PROBE_UNSUPPORTED:
        return (
            f"{reference}: NOT VERIFIED -- this container tool does not support the "
            f"reachability probe a dry run uses."
        )
    if outcome.cause is Cause.NOT_PULLED:
        return (
            f"{reference}: NOT VERIFIED -- reachable, but its Python version cannot be "
            f"read without pulling it, which a dry run does not do."
        )
    return f"{reference}: {outcome.verdict.value}"


# Ordered because the patterns overlap: a 403 that also mentions a manifest is still an
# authorization failure, and "no space left on device" arrives wrapped in a write error.
_ERROR_PATTERNS = (
    (
        Cause.PROBE_UNSUPPORTED,
        ("is not a docker command", "unknown command", "unrecognized command"),
    ),
    (
        Cause.UNAUTHORIZED,
        (
            "unauthorized",
            "authentication required",
            "denied",
            "403",
            "forbidden",
            "login",
        ),
    ),
    (Cause.NO_DISK, ("no space left on device", "disk quota exceeded")),
    (
        Cause.UNKNOWN_TAG,
        ("manifest unknown", "not found", "manifest for", "does not exist"),
    ),
    (
        Cause.UNREACHABLE,
        (
            "no such host",
            "connection refused",
            "i/o timeout",
            "timeout",
            "network",
            "temporary failure",
        ),
    ),
)


def classify_fetch_error(stderr: str) -> Cause:
    """Map a container tool's failure output onto the cause that names the right remedy.

    An unrecognised failure returns ``UNREACHABLE``, never success. Availability has no
    unknown outcome: a failure nobody has catalogued is still a failure, and guessing that it
    is benign would let the teardown proceed on an assumption.
    """
    text = (stderr or "").lower()
    for cause, needles in _ERROR_PATTERNS:
        if any(needle in text for needle in needles):
            return cause
    return Cause.UNREACHABLE


class ContainerProbe:
    """The only part of this module that touches the container tool.

    Everything else takes probe results as data, so the whole decision surface is unit-tested
    without a daemon. ``manifest inspect`` appears here only for the dry path -- the real path
    pulls, because ``pull`` and ``image inspect`` are uniformly supported where
    ``manifest inspect`` is not (design D2).
    """

    def __init__(self, container_tool: str = "docker", timeout: int = 600):
        self.container_tool = container_tool
        self.timeout = timeout

    def _run(self, args: Sequence[str], timeout: Optional[int] = None):
        """Run one probe command. Returns the result, or a Cause when it could not run.

        A timeout is deliberately distinguished from every other failure. Collapsing the two
        made a wedged `manifest inspect` report itself as "this container tool does not
        support the probe", which sends the operator hunting for a tooling problem that does
        not exist while the real fault -- a slow or unhealthy registry -- goes unnamed.
        """
        import subprocess

        try:
            return subprocess.run(
                [self.container_tool, *args],
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired:
            return Cause.UNREACHABLE
        except Exception:  # noqa: BLE001 - a probe must never raise into `archi create`
            return None

    @staticmethod
    def _ran(result) -> bool:
        """True when ``_run`` produced a completed process rather than a Cause."""
        return result is not None and not isinstance(result, Cause)

    def runtime_available(self) -> bool:
        result = self._run(["version", "--format", "{{.Client.Version}}"], timeout=10)
        if self._ran(result) and result.returncode == 0:
            return True
        # Fall back on a non-zero exit too, not only on an exception: a tool that does not
        # implement `version --format` exits non-zero, and treating that as "no runtime"
        # would refuse a real create on a perfectly usable daemon.
        result = self._run(["--version"], timeout=10)
        return bool(self._ran(result) and result.returncode == 0)

    def image_present(self, reference: str) -> bool:
        result = self._run(["image", "inspect", reference], timeout=30)
        return bool(self._ran(result) and result.returncode == 0)

    def pull(self, reference: str) -> Optional[Cause]:
        result = self._run(["pull", reference])
        if isinstance(result, Cause):
            return result
        if result is None:
            return Cause.UNREACHABLE
        if result.returncode == 0:
            return None
        return classify_fetch_error(result.stderr or result.stdout)

    def reachable(self, reference: str) -> Optional[Cause]:
        result = self._run(["manifest", "inspect", reference], timeout=60)
        if isinstance(result, Cause):
            return result
        if result is None:
            # The command could not be launched at all, which for `manifest` most often means
            # the subcommand does not exist on this tool. A timeout took the branch above.
            return Cause.PROBE_UNSUPPORTED
        if result.returncode == 0:
            return None
        return classify_fetch_error(result.stderr or result.stdout)

    def python_version(self, reference: str) -> Optional[str]:
        result = self._run(
            ["run", "--rm", "--entrypoint", "python", reference, "-V"], timeout=120
        )
        if not self._ran(result) or result.returncode != 0:
            return None
        return (result.stdout or result.stderr or "").strip() or None


def run_preflight(
    references: Sequence[str],
    *,
    probe,
    floor: str,
    dry: bool = False,
) -> List[Outcome]:
    """Decide every reference, in the order the invariant requires.

    Availability first, because a version cannot be read from an image that is not there --
    attempting it would report an unreadable version where the real cause is a failed pull.
    The floor check then runs for every image that ended up present, on a real create and on
    a dry run alike (design D5).
    """
    runtime = probe.runtime_available()
    outcomes: List[Outcome] = []

    for reference in references:
        present = runtime and probe.image_present(reference)
        fetch_cause = None
        if runtime and not present and not reference.startswith(LOCAL_PREFIX):
            fetch_cause = probe.reachable(reference) if dry else probe.pull(reference)
            if not dry and fetch_cause is None:
                present = True

        outcome = decide_availability(
            reference,
            runtime_available=runtime,
            present_locally=present,
            fetch_cause=fetch_cause,
            dry=dry,
        )

        if outcome.verdict is Verdict.AVAILABLE and present:
            outcome = check_python_floor(
                reference, probe.python_version(reference), floor
            )

        outcomes.append(outcome)

    return outcomes


class BaseImagePreflightError(Exception):
    """Raised when a base image cannot be established, before anything is destroyed."""


def _refuse_uncoverable_templates(template_dir: Optional[Path] = None) -> None:
    """Refuse when a service template declares no ``a2rchi-*-base`` FROM reference.

    Shared by both entry points deliberately. The refusal first landed only in
    ``required_base_images``, which has no production caller, so the deploy path went on
    silently (fasrc/archi#381) -- the fail-open this module exists to remove.
    """
    uncoverable = templates_missing_base_reference(template_dir)
    if uncoverable:
        raise BaseImagePreflightError(
            "Base image check failed: the following service templates declare no "
            "a2rchi-*-base FROM reference, so the preflight cannot cover them:\n"
            + "\n".join(f"  {p}" for p in uncoverable)
        )


def enforce_base_images(
    compose_config,
    *,
    use_podman: bool = False,
    dry: bool = False,
    probe=None,
    template_dir: Optional[Path] = None,
    pyproject_path: Optional[Path] = None,
) -> List[Outcome]:
    """The single entry point `archi create` calls. Raises rather than returning a failure.

    Kept here, not in ``cli_main``, for two reasons: the decision logic stays unit-testable
    without invoking the CLI, and lines added to ``cli_main`` are not imported by the unit
    suite, so they would fail the diff-coverage gate (design D8).

    Returns the outcomes so a dry run can report what it could not verify.
    """
    container_tool = "podman" if use_podman else "docker"
    probe = probe or ContainerProbe(container_tool)

    try:
        grader_enabled = bool(compose_config.get_service("grader").enabled)
    except ValueError:
        # `ComposeConfig.get_service` raises ValueError for a name it does not know
        # (`service_registry.py:213`, `service_builder.py:110`), which is the one case worth
        # tolerating: a plan with no grader simply has no grader. Anything else is a real
        # fault and must surface -- swallowing it would silently skip the pytorch check for a
        # grader deployment and land on teardown-then-fail. `KeyError` is deliberately NOT
        # tolerated: nothing on the "no such service" path raises it, so a KeyError here is a
        # dict-backed plan that lost a key, and reading that as "grader disabled" is the
        # fail-open this whole module exists to remove.
        grader_enabled = False

    # Before the derivation below, not after: `base_reference` returns the first match in
    # *any* template, so a healthy template masks a broken one from this point on. Before
    # `run_preflight` too, and therefore before `remove_existing_deployment()`
    # (`cli_main.py:294`) -- the ordering contract from #287.
    _refuse_uncoverable_templates(template_dir)

    names = required_base_image_names(
        getattr(compose_config, "gpu_ids", None), grader_enabled
    )

    references = []
    unresolved = []
    for image in names:
        reference = base_reference(image, template_dir)
        if reference:
            references.append(reference)
        else:
            unresolved.append(image)

    if unresolved:
        # Not "nothing to check" -- the rule says these are required and the templates do not
        # declare them. Returning an empty set here would disable the preflight on a template
        # rename or a drifted `FROM` pattern, and `--force` would tear down a working
        # deployment having established nothing.
        raise BaseImagePreflightError(
            "Base image check failed:\n"
            f"  This deployment requires {', '.join(unresolved)}, but no service template "
            f"under {template_dir or TEMPLATE_DIR} declares a FROM line for it.\n"
            f"  The preflight cannot verify an image it cannot name, and will not proceed "
            f"as though there were nothing to check."
        )

    outcomes = run_preflight(
        references,
        probe=probe,
        floor=declared_python_floor(pyproject_path),
        dry=dry,
    )

    refused = [outcome for outcome in outcomes if outcome.refused]
    if refused:
        raise BaseImagePreflightError(
            "Base image check failed:\n" + summarize(refused, container_tool)
        )
    return outcomes


def unverified_notes(
    outcomes: Sequence[Outcome], container_tool: str = "docker"
) -> List[str]:
    """The NOT VERIFIED lines a dry run must show instead of claiming readiness."""
    return [
        compose_message(outcome, container_tool)
        for outcome in outcomes
        if outcome.unverified
    ]


def _source_pyproject() -> Path:
    """Where ``pyproject.toml`` sits when archi runs from a source checkout."""
    return Path(__file__).resolve().parents[2].parent / "pyproject.toml"


def _read_pyproject_floor(path: Path) -> str:
    """The ``requires-python`` declared in one pyproject file.

    Raises rather than returning ``None``. A file that exists but cannot be read or parsed is
    not the same thing as a file that is not there, and the caller has to tell them apart:
    conflating them turns a broken checkout into a silent fallback onto possibly stale
    metadata, which is a fail-open on the very check this module performs.
    """
    import tomllib

    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError) as error:
        raise BaseImagePreflightError(
            f"Could not read the project's requires-python floor from {path}: {error}. "
            f"Refusing rather than falling back to a floor that may be out of date."
        ) from error

    try:
        return data["project"]["requires-python"]
    except (KeyError, TypeError) as error:
        raise BaseImagePreflightError(
            f"{path} declares no [project] requires-python, so the base image cannot be "
            f"checked against a floor."
        ) from error


def _metadata_python_floor() -> Optional[str]:
    """The floor recorded in the installed distribution's metadata, if any."""
    try:
        from importlib.metadata import PackageNotFoundError, metadata

        return metadata("archi")["Requires-Python"] or None
    except Exception:  # noqa: BLE001 - metadata lookup must not break `archi create`
        return None


def declared_python_floor(pyproject_path: Optional[Path] = None) -> str:
    """The project's own ``requires-python``, read rather than duplicated as a constant.

    Resolution order matters, and is not the obvious one:

    1. An explicitly supplied path, for tests.
    2. ``pyproject.toml`` from the source checkout, when there is one.
    3. The installed distribution's metadata.

    The source tree outranks installed metadata because that metadata can be stale, and in
    practice is: an editable install made before the floor was corrected still advertises
    ``>=3.7``. Trusting it would let the preflight accept the Python 3.10 base image this
    module exists to reject, and do so silently.

    Metadata is nonetheless a necessary fallback. A non-editable install puts this file under
    ``site-packages``, where the computed source path lands on a ``pyproject.toml`` that is
    not shipped; reading it unguarded would fail every ``archi create`` on an installed CLI,
    ``--dry`` included.
    """
    if pyproject_path is not None:
        return _read_pyproject_floor(Path(pyproject_path))

    source = _source_pyproject()
    if source.exists():
        # Present but broken fails closed, on purpose. See _read_pyproject_floor.
        return _read_pyproject_floor(source)

    floor = _metadata_python_floor()
    if floor:
        return floor

    raise BaseImagePreflightError(
        "Could not determine this project's requires-python floor from either "
        f"{source} or the installed package metadata, so the base image cannot be "
        "checked against it."
    )


def summarize(outcomes: Sequence[Outcome], container_tool: str = "docker") -> str:
    """Join the messages for every outcome that is not plainly available."""
    return "\n".join(
        compose_message(outcome, container_tool)
        for outcome in outcomes
        if outcome.verdict is not Verdict.AVAILABLE
    )
