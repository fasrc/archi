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
    """
    images = [PYTHON_BASE]
    if gpu_ids or grader_enabled:
        images.append(PYTORCH_BASE)

    references = []
    for image in images:
        reference = base_reference(image, template_dir)
        if reference:
            references.append(reference)
    return references


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
    from packaging.version import InvalidVersion, Version

    version_text = _parse_version(reported)
    if version_text is None:
        return Outcome(
            reference,
            Verdict.REFUSED,
            Cause.VERSION_UNREADABLE,
            detail=(reported or "").strip(),
        )

    try:
        version = Version(version_text)
    except InvalidVersion:
        return Outcome(
            reference, Verdict.REFUSED, Cause.VERSION_UNREADABLE, detail=version_text
        )

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


def summarize(outcomes: Sequence[Outcome], container_tool: str = "docker") -> str:
    """Join the messages for every outcome that is not plainly available."""
    return "\n".join(
        compose_message(outcome, container_tool)
        for outcome in outcomes
        if outcome.verdict is not Verdict.AVAILABLE
    )
