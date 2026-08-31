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

# The bases this preflight can probe. The coverage check
# (templates_missing_base_reference) accepts a template only when its final stage names a
# member, so a base that is in here but that `required_base_image_names` never asks for
# would be accepted and then never probed. That rule cannot be derived from this set --
# which base a deployment needs depends on the GPU and grader flags, not on set membership
# (design D4) -- so the two declarations are held together by a guard test instead:
# `test_every_placeable_base_is_reachable_from_the_two_image_rule`.
PLACEABLE_BASES = frozenset({PYTHON_BASE, PYTORCH_BASE})

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

# Every `FROM <ref> [AS <alias>]` line. One matcher for both readers -- the coverage check
# and `base_reference` -- so they cannot disagree about what a template's base is. `\S+`
# stops at whitespace, so the trailing spaces several templates carry never reach the tag.
# `(?:--\S+\s+)*` skips the flags a FROM may carry -- `--platform=$BUILDPLATFORM` above all,
# a form the release rewriter already recognizes
# (`scripts/dev/update_service_base_images.py:105`). Without it the flag is captured as the
# reference and the preflight refuses a template the rest of the toolchain supports.
# `[ \t]*`, not `\s*`: Docker accepts an indented instruction, but under re.MULTILINE
# `\s` also matches the newline, which would let this walk past a blanked line and read the
# next one as a stage -- reintroducing exactly what the blanking prevents.
_FROM_STAGE_RE = re.compile(
    r"^[ \t]*FROM\s+(?:--\S+\s+)*(?P<ref>\S+)(?:\s+AS\s+(?P<alias>\S+))?",
    re.MULTILINE | re.IGNORECASE,
)

LOCAL_PREFIX = "localhost/"

# A heredoc opener: `RUN <<EOF`, `RUN <<-EOF`, `COPY <<"EOF" /f`, and the forms that put
# whitespace or a file descriptor around the operator -- `RUN << EOF`, `RUN 3<<EOF`,
# `RUN <<- EOF`. All are the same redirection, and a pattern that reads only the tight form
# leaves the payload scanned as instructions.
#
# The leading `(?:^|\s)` and the trailing lookahead are what keep shell text out: `$((1<<n))`
# is not preceded by whitespace, and in `echo "a << b"` the closing quote follows the tag, so
# neither reads as an opener. Over-reading one is no longer a silent pass in any case -- an
# opener whose delimiter never arrives now fails closed (`_instruction_text`).
#
# The tag is `[\w-]+`, not `[A-Za-z_][\w-]*`: Docker's delimiter is any word, so `RUN cat
# <<123` is a real heredoc, and demanding a leading letter leaves its payload scanned as
# instructions -- which is how an a2rchi-looking `FROM` in shell text hides a third-party
# final stage. Accepting a digit widens the over-read the other way too: spaced arithmetic
# (`$(( 1 << 3 ))`) now reads as an opener and fails closed. That trade is the module's
# standing one -- a loud refusal over a silent pass -- and it already applied to the same
# form with a letter operand (`$(( 1 << n ))`).
_HEREDOC_RE = re.compile(
    r"(?:^|\s)\d*<<(?P<dash>-?)\s*(?P<q>['\"]?)(?P<tag>[\w-]+)(?P=q)(?=\s|$)"
)

# Docker's `# escape=` parser directive, which chooses the line-continuation character.
# Only a backslash or a backtick is allowed.
_ESCAPE_DIRECTIVE_RE = re.compile(
    r"^#\s*escape\s*=\s*(?P<char>[\\`])\s*$", re.IGNORECASE
)

# Any parser directive -- `# syntax=...`, `# escape=...`, `# check=...`. Docker stops looking
# for directives at the first line that is not one, so this is what lets the scan step over
# the `# syntax=docker/dockerfile:1` every template in this repo opens with, and stop at the
# ordinary `# base-image-pin:` comment under it.
_PARSER_DIRECTIVE_RE = re.compile(r"^#\s*[a-z]+\s*=\s*\S+\s*$", re.IGNORECASE)


def _escape_char(text: str) -> str:
    """The line-continuation character ``text`` declares, or the default backslash.

    Docker reads parser directives only at the very top of a file: the first comment that is
    not a directive, the first instruction, or the first blank line ends the section, and an
    ``# escape=`` below that is an ordinary comment. Reading a late one would promote a
    genuine continuation line to a build stage and refuse a correctly based template.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or not stripped.startswith("#"):
            break
        match = _ESCAPE_DIRECTIVE_RE.match(stripped)
        if match:
            return match.group("char")
        if not _PARSER_DIRECTIVE_RE.match(stripped):
            break
    return "\\"


# Only these instructions can open a heredoc. Without this, `# Example: RUN <<EOF` in an
# ordinary comment blanks every instruction after it, up to a delimiter line that may never
# come -- which hides a third-party final stage behind an earlier a2rchi builder stage.
_HEREDOC_INSTRUCTION_RE = re.compile(r"^(?:RUN|COPY|ADD)\b", re.IGNORECASE)


def _heredoc_delimiters(line: str) -> List[tuple]:
    """``(delimiter, strips_tabs)`` for every heredoc ``line`` opens, in declaration order.

    ``strips_tabs`` is the ``<<-`` form, which lets the terminator carry leading **tabs** --
    and only tabs. Spaces never indent a terminator in either form.
    """
    return [(m.group("tag"), bool(m.group("dash"))) for m in _HEREDOC_RE.finditer(line)]


def _instruction_text(text: str) -> Optional[str]:
    """``text`` with every line that does not start an instruction blanked.

    Three kinds of line look like an instruction to a line-oriented regex and are not one:

    - a **heredoc body**. `RUN <<EOF` opens shell text, so a payload line beginning with
      ``FROM`` is not a build stage.
    - a **continuation**. `RUN echo hello \\` and the line under it are one command to
      Docker, however that next line begins.
    - a **comment**. `# Example: RUN <<EOF` is prose; reading it as a heredoc opener blanks
      every instruction after it, up to a delimiter line that may never come.

    Each reads both ways, and the second way is the dangerous one: a third-party-looking
    line refuses a correctly based template, while an a2rchi-looking line hides a
    third-party final stage and marks the template covered -- a silent pass on an
    assumption, which is the one thing this module may not do.

    An instruction may open more than one heredoc -- `RUN <<ONE <<TWO` -- and Docker feeds
    the payloads in the order they are declared, so every delimiter is collected and
    consumed in that order.

    A heredoc may also be opened on a **continuation** of the instruction -- `RUN \\` then
    `<<EOF` -- so continuation lines are read for openers even though they are blanked.
    Blanking one without recording its delimiter would leave the payload scanned as
    instructions, which is how an a2rchi `FROM` inside shell text hides a third-party final
    stage.

    Returns ``None`` when a delimiter never arrives. That is the "could not tell" case, and
    it is why it fails closed rather than returning the text it managed to blank: an
    unterminated heredoc swallows the rest of the template, so the last stage still standing
    is whatever preceded it -- an a2rchi builder, reading as covered. It costs a correct
    template nothing, because a correct template terminates its heredocs. It is also what
    keeps a quoted `<<EOF` in ordinary shell text (`RUN echo "example <<EOF here"`) from
    passing silently: this walk cannot see shell quoting, so it over-reads the opener, finds
    no terminator, and refuses instead of guessing.

    The continuation character is whatever the ``# escape=`` parser directive at the top of
    the file declares, defaulting to the backslash. Hard-coding the backslash reads a
    backtick-continued `RUN` as a finished instruction, which promotes the line under it to a
    build stage -- another way a third-party final stage hides behind an a2rchi builder.

    Docker removes a full-line comment before it joins a continuation, and so does this: a
    comment inside a continuation neither ends the instruction nor opens a heredoc. Ending
    it there read `RUN echo x \\`, a comment, then `FROM <a2rchi base>` as a build stage
    while Docker had joined all three into one `RUN` -- the third-party stage above it is
    what ships, so the template passed unprobed.

    The bounds, stated rather than implied. This walk cannot see shell quoting or word
    splitting, so it over-reads a heredoc opener in text that only looks like one
    (`$(( 1 << 3 ))`) and refuses. That direction is the deliberate one: a refusal is loud
    and a silent pass is not.
    """
    escape = _escape_char(text)
    lines = []
    pending: List[tuple] = []
    continued = False
    for line in text.splitlines():
        if pending:
            lines.append("")
            delimiter, strips_tabs = pending[0]
            # Docker ends `<<EOF` only on a line that *is* the delimiter; `<<-EOF` allows
            # leading tabs. Neither allows leading spaces, so `line.strip()` would end the
            # payload early and scan the rest of it as instructions.
            if (line.lstrip("\t") if strips_tabs else line) == delimiter:
                pending.pop(0)
            continue
        if continued:
            lines.append("")
            # Docker removes a full-line comment *before* it joins the continuation, so the
            # comment neither ends the instruction nor opens a heredoc. Ending it here
            # promoted the next line to a build stage: `RUN echo x \`, a comment, then
            # `FROM <a2rchi base>` read as covered while the stage that actually ships is
            # the third-party image above -- a silent pass. Reading the comment for an
            # opener is the mirror fault, blanking the rest of the file for a line Docker
            # discarded.
            if line.strip().startswith("#"):
                continue
            pending = _heredoc_delimiters(line)
            continued = not pending and line.rstrip().endswith(escape)
            continue

        lines.append(line)
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if _HEREDOC_INSTRUCTION_RE.match(stripped):
            pending = _heredoc_delimiters(line)
        if not pending:
            continued = stripped.endswith(escape)
    if pending:
        return None
    return "\n".join(lines)


def _reference_names(reference: str, image: str) -> bool:
    """True when ``reference`` names exactly ``image``, not merely contains it.

    A registry prefix ends at ``/`` and a tag or digest starts at ``:`` or ``@``, so those
    are the only characters allowed to touch the name. Without this,
    ``ghcr.io/other/a2rchi-python-base-custom`` reads as the python base: the coverage check
    calls the template covered and ``base_reference`` hands the probe an image no template
    builds from.
    """
    return re.search(rf"(?:^|/){re.escape(image)}(?:[:@]|$)", reference) is not None


def _names_placeable_base(reference: str) -> bool:
    """True when ``reference`` names a base in ``PLACEABLE_BASES`` at image boundaries."""
    return any(_reference_names(reference, base) for base in PLACEABLE_BASES)


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


def _final_stage_base(text: str) -> Optional[str]:
    """The base reference the final stage of a Dockerfile actually runs on.

    Parses all FROM lines in order and follows alias chains to find the ultimate base
    reference. Heredoc bodies are blanked first, so a `RUN <<EOF` payload line beginning
    with ``FROM`` is not read as a stage. Handles a linear chain of named stages. Does NOT
    handle ARG substitution, build args, --platform flags, or COPY --from provenance — the
    bound is intentional. Stating the bound rather than implying totality is what the
    original defect lacked.

    Returns None when the template has no FROM lines, when a heredoc is never terminated,
    or when the parser encounters a reference it cannot resolve. Returning None fails closed:
    the caller treats an unresolvable template as uncovered.
    """
    instructions = _instruction_text(text)
    if instructions is None:
        return None
    stages = [
        (m.group("ref"), (m.group("alias") or "").lower())
        for m in _FROM_STAGE_RE.finditer(instructions)
    ]
    if not stages:
        return None

    alias_map = {alias: ref for ref, alias in stages if alias}

    ref = stages[-1][0]
    visited: set = set()
    # Follow alias chains: `FROM build` where `build` was named by an earlier stage.
    while ref.lower() in alias_map:
        if ref.lower() in visited:
            return None  # cycle guard — malformed template cannot hang the preflight
        visited.add(ref.lower())
        ref = alias_map[ref.lower()]

    return ref


def uncoverable_template_reasons(
    template_dir: Optional[Path] = None,
) -> List[tuple]:
    """``(template, reason)`` for every service template the preflight cannot cover.

    The reason is what the operator has to act on, and the three cases call for three
    different actions: restore a missing ``FROM`` line, move a final stage back onto a
    supported base, or add the named base to ``PLACEABLE_BASES`` and to the two-image rule.
    Reporting all three as "declares no a2rchi-*-base reference" sends the reader of the
    latter two looking for a line that is already there.
    """
    reasons = []
    for template in service_templates(template_dir):
        base = _final_stage_base(template.read_text())
        if base is None:
            reasons.append(
                (template, "no FROM line the preflight can resolve to a base image")
            )
        elif _names_placeable_base(base):
            continue
        elif "a2rchi-" in base:
            reasons.append(
                (
                    template,
                    f"final stage builds on {base}, which is not one of the bases the "
                    f"preflight can probe ({', '.join(sorted(PLACEABLE_BASES))})",
                )
            )
        else:
            reasons.append(
                (
                    template,
                    f"final stage builds on {base}, which is not an a2rchi base image",
                )
            )
    return reasons


def templates_missing_base_reference(template_dir: Optional[Path] = None) -> List[Path]:
    """Service templates whose final stage does not name a base in ``PLACEABLE_BASES``.

    A non-empty return means a service template has lost its base ``FROM`` line, replaced
    it with a third-party image, names an a2rchi base the preflight cannot probe, or (for
    multistage templates) has a final stage that does not run on an a2rchi base.  The
    caller should treat a non-empty list as a refusal to proceed.  Use
    ``uncoverable_template_reasons`` when the caller has to tell the operator which.
    """
    return [template for template, _ in uncoverable_template_reasons(template_dir)]


def two_image_rule_offenders(template_dir: Optional[Path] = None) -> List[str]:
    """Service templates whose final-stage base contradicts the two-image rule (design D4).

    The rule `required_base_image_names` applies — python always, pytorch for a ``-gpu``
    variant or the grader — is a claim about the templates, and it is made against the
    stage that ships. Reading the first ``FROM`` instead misses the drift that matters: a
    non-GPU template with ``FROM <python-base> AS builder`` ending on the pytorch base is
    accepted by the coverage check, asked for python only, and so deploys on an image the
    preflight never probed.
    """
    offenders = []
    for template in service_templates(template_dir):
        base = _final_stage_base(template.read_text())
        if base is None:
            continue  # uncoverable_template_reasons reports this, with its own diagnosis
        name = template.name
        is_gpu = name.endswith("-gpu")
        if _reference_names(base, PYTORCH_BASE) and not (
            is_gpu or name == "Dockerfile-grader"
        ):
            offenders.append(f"{name}: pytorch base but neither -gpu nor the grader")
        if _reference_names(base, PYTHON_BASE) and is_gpu:
            offenders.append(f"{name}: -gpu variant on the python base")
    return offenders


def base_reference(image: str, template_dir: Optional[Path] = None) -> Optional[str]:
    """The pinned reference the service templates ship ``image`` at.

    Read from the templates rather than composed from a constant, so the preflight cannot
    check a different tag than the one the build will use. It reads each template's *final
    stage* -- the same judgement ``templates_missing_base_reference`` makes -- because a
    template may name the same base twice, one digest in a builder stage and another in the
    stage that ships. Probing the builder's line would establish an image the deployment
    never runs.

    Bound, tracked as fasrc/archi#389: the first service template that ships ``image`` wins.
    Two templates shipping the same base at different references is a split pin that nothing
    here refuses yet.
    """
    for template in service_templates(template_dir):
        base = _final_stage_base(template.read_text())
        if base is not None and _reference_names(base, image):
            return base
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
    """The rule itself, with no filesystem in it: which base images this deployment needs.

    Both names are members of ``PLACEABLE_BASES``, the single source of which a2rchi bases
    exist.  The python base is always required; the pytorch base only when a GPU is
    requested or the grader is enabled (design D4).
    """
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
    """Refuse when a service template's final stage is not a base the preflight can probe.

    Shared by both entry points deliberately. The refusal first landed only in
    ``required_base_images``, which has no production caller, so the deploy path went on
    silently (fasrc/archi#381) -- the fail-open this module exists to remove.
    """
    uncoverable = uncoverable_template_reasons(template_dir)
    if uncoverable:
        raise BaseImagePreflightError(
            "Base image check failed: the preflight cannot cover the following service "
            "templates:\n"
            + "\n".join(f"  {path}: {reason}" for path, reason in uncoverable)
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
