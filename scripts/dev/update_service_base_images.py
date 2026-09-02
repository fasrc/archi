#!/usr/bin/env python3
"""Update service Dockerfiles to reference a specific base image tag."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES_DIR = PROJECT_ROOT / "src" / "cli" / "templates" / "dockerfiles"

BASE_IMAGE_MAP = {
    "python": "a2rchi-python-base",
    "pytorch": "a2rchi-pytorch-base",
}

SOURCE_PREFIXES = {
    "localhost": "localhost/a2rchi/",
    "dockerhub": "docker.io/a2rchi/",
    "ghcr": "ghcr.io/fasrc/",
}


@dataclass
class UpdateOptions:
    tag: Optional[str]
    orig_tag: Optional[str]
    switch_source: Optional[str]
    bases: Iterable[str]
    # Keyed by image name, not by the `--digest` name, so `_update_line` can
    # look a digest up with the base name it already holds.
    digests_by_image: Dict[str, str] = field(default_factory=dict)
    # Check the references instead of writing them. See `verify_base_tags`.
    verify: bool = False


def _normalize_prefix(prefix: str) -> str:
    """Return a registry/image prefix with a single trailing slash or empty."""
    cleaned = "/".join(filter(None, prefix.split("/")))
    if cleaned:
        return cleaned.rstrip("/") + "/"
    return ""


def _split_image_spec(image_spec: str) -> Tuple[str, str, Optional[str], Optional[str]]:
    """Split a base reference into (prefix, image, tag, digest).

    Handles both reference forms: "<prefix><image>:<tag>" and
    "<prefix><image>@sha256:<hex>". They are alternatives, not layers, so at
    most one of `tag` and `digest` is set. The digest is cut off first because
    it contains a ":" that the tag split would otherwise claim.

    A reference may carry both, as in "<image>:<tag>@sha256:<hex>". The digest
    decides which image is pulled and the tag beside it is informational, so
    the reference is reported as digest-pinned and that tag is dropped. Leaving
    it glued to the image name would make the name fail every caller's
    comparison and the line would be skipped in silence.
    """
    if "@" in image_spec:
        repo_part, digest = image_spec.rsplit("@", 1)
        tag: Optional[str] = None
    else:
        digest = None
        if ":" in image_spec:
            repo_part, tag = image_spec.rsplit(":", 1)
        else:
            repo_part, tag = image_spec, None

    repo_part = repo_part.replace("//", "/")
    segments = [seg for seg in repo_part.split("/") if seg]
    if not segments:
        return "", repo_part, tag, digest

    image = segments[-1]
    if digest is not None and ":" in image:
        # A tag rode along with the digest. Only the last segment can carry
        # one, so a registry port earlier in the reference is left alone.
        image = image.split(":", 1)[0]
    prefix = "/".join(segments[:-1])
    if prefix:
        prefix += "/"

    return prefix, image, tag, digest


def _build_image_spec(
    prefix: str, image: str, tag: Optional[str], digest: Optional[str] = None
) -> str:
    """Build a base reference. A digest wins over a tag — they never combine."""
    prefix = _normalize_prefix(prefix)
    repo = f"{prefix}{image}" if prefix else image
    if digest:
        return f"{repo}@{digest}"
    if tag:
        return f"{repo}:{tag}"
    return repo


# One matcher for both the rewrite and the verification. A verification that
# disagreed with the rewriter about what counts as a base line would pass a
# line the rewriter never touched, which is the failure `--verify` exists to
# catch.
_FROM_RE = re.compile(
    r"(?P<intro>\s*FROM\s+(?:--platform=\S+\s+)?)(?P<image>\S+)(?P<suffix>.*)"
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")

# An OCI image tag, per the distribution spec's grammar.
_TAG_RE = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}")

# The annotation this script writes ABOVE a digest-pinned FROM line. It cannot
# go on the FROM line itself: a Dockerfile recognises "#" as a comment only at
# the start of a line, so a trailing "# tag" becomes a second FROM argument and
# both docker and podman reject the file with "FROM requires either one or
# three arguments".
#
# The wording names the script that owns the line, so a template's own comment
# cannot be mistaken for one of these and deleted. Both halves must match for
# the script to treat a line as its own.
ANNOTATION_PREFIX = "# base-image-pin: "
ANNOTATION_SUFFIX = " (managed by update_service_base_images.py)"

_ANNOTATION_RE = re.compile(
    rf"^\s*{re.escape(ANNOTATION_PREFIX)}\S+{re.escape(ANNOTATION_SUFFIX)}\s*$"
)


def _split_line_ending(line: str) -> Tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def _is_annotation(line: Optional[str]) -> bool:
    """Report whether a line is an annotation this script wrote."""
    if line is None:
        return False
    core, _ = _split_line_ending(line)
    return bool(_ANNOTATION_RE.match(core))


def _update_line(
    line: str,
    base_name: str,
    options: UpdateOptions,
    current_annotation: Optional[str] = None,
) -> Tuple[Optional[str], str, bool]:
    """Rewrite one FROM line and the annotation line that sits above it.

    Returns (annotation, line, changed). `annotation` is the line to write
    above this one, or None for no annotation. `changed` covers both, so a
    rewrite that moves only the annotation still reports as a change.
    """
    core, newline = _split_line_ending(line)
    stripped = core.lstrip()
    if not stripped.startswith("FROM "):
        return current_annotation, line, False

    match = _FROM_RE.match(core)
    if not match:
        return current_annotation, line, False

    intro, image_spec, suffix = (
        match.group("intro"),
        match.group("image"),
        match.group("suffix"),
    )
    prefix, image, current_tag, current_digest = _split_image_spec(image_spec)

    if image != base_name:
        return current_annotation, line, False

    if options.orig_tag is not None and current_tag != options.orig_tag:
        return current_annotation, line, False

    target_tag = options.tag if options.tag is not None else current_tag
    target_prefix = prefix
    if options.switch_source:
        target_prefix = SOURCE_PREFIXES[options.switch_source]

    target_digest = options.digests_by_image.get(image)

    # A rewrite that names no new reference — a bare `--switch-source`, say —
    # has nothing to put in the old one's place. Without this the reference
    # would be rebuilt from prefix and image alone, silently unpinning the base
    # to a bare `FROM <repo>` that resolves to `latest` at build time.
    if target_digest is None and options.tag is None:
        target_digest = current_digest

    # The annotation survives exactly when the digest does not move and no new
    # tag is given to name. Since `--digest` requires `--tag`, that is the
    # rewrite which names no new reference at all — a bare `--switch-source` —
    # and the digest it leaves in place still points at the build the
    # annotation names.
    keeping_digest = (
        options.tag is None
        and current_digest is not None
        and target_digest == current_digest
    )

    if target_digest is not None:
        # A digest says nothing about which build it is, so the tag from
        # `--tag` is recorded above it. `parse_args` requires `--tag` alongside
        # `--digest`, so a build name is always available here.
        updated_spec = _build_image_spec(target_prefix, image, None, target_digest)
        if keeping_digest:
            updated_annotation = current_annotation
        else:
            indent = intro[: len(intro) - len(intro.lstrip())]
            # The FROM line can be the last in a file and carry no line ending.
            # Reusing that would glue the two together as "# ...tagFROM ...",
            # commenting the base instruction out. "\n" is always right here:
            # `update_base_tags` reads with `Path.read_text()`, whose universal
            # newlines leave no "\r" for any line to carry.
            separator = newline or "\n"
            updated_annotation = (
                f"{indent}{ANNOTATION_PREFIX}{options.tag}"
                f"{ANNOTATION_SUFFIX}{separator}"
            )
    else:
        updated_spec = _build_image_spec(target_prefix, image, target_tag)
        # An annotation names the build a digest is. A tag reference names its
        # own build, so an annotation above one labels nothing and goes —
        # whatever the reference it replaced.
        updated_annotation = None

    # The FROM line's trailing text — a build stage name, a stray space — is
    # never this script's to touch, because the annotation no longer lives
    # there.
    updated_line = f"{intro}{updated_spec}{suffix}{newline}"
    changed = updated_line != line or updated_annotation != current_annotation
    return updated_annotation, updated_line, changed


def _annotation_index(out: list) -> Optional[int]:
    """Find the annotation belonging to the line about to be emitted.

    It is normally the line just written, but a blank line can sit between the
    annotation and its FROM line. Stopping at the blank would hide the
    annotation from the rewrite and leave it naming a build the file no longer
    references.
    """
    index = len(out) - 1
    while index >= 0 and not out[index].strip():
        index -= 1
    if index >= 0 and _is_annotation(out[index]):
        return index
    return None


def _rewrite_lines(
    lines: Iterable[str], base_name: str, options: UpdateOptions
) -> Tuple[list, bool]:
    """Rewrite every FROM line for one base, and the annotation above it."""
    out: list = []
    changed = False

    for line in lines:
        index = _annotation_index(out)
        current_annotation = out[index] if index is not None else None

        annotation, new_line, line_changed = _update_line(
            line, base_name, options, current_annotation
        )

        if line_changed:
            if index is not None:
                out.pop(index)
            if annotation is not None:
                # Always directly above the FROM line, whatever sat between
                # them before.
                out.append(annotation)
            changed = True

        out.append(new_line)

    return out, changed


def _validated_bases(bases: Iterable[str]) -> list:
    """The requested base types, or an error naming the ones that do not exist."""
    requested = list(bases)
    invalid = set(requested) - set(BASE_IMAGE_MAP)
    if invalid:
        raise SystemExit(f"Unknown base types requested: {', '.join(sorted(invalid))}")
    return requested


def verify_base_tags(options: UpdateOptions) -> None:
    """Fail unless every service base reference already names the target.

    The release workflow calls this straight after the rewrite. It reads the
    reference each template declares rather than whether the file changed. The
    two tests disagree on exactly one input — a template that already carries
    the target reference — and there the reference is right and the diff is
    empty. That is what a re-dispatch of a release tag produces once an earlier
    run of the same tag pushed its Dockerfile commit.
    """
    image_names = {BASE_IMAGE_MAP[base] for base in _validated_bases(options.bases)}
    source_prefix = (
        SOURCE_PREFIXES[options.switch_source] if options.switch_source else None
    )

    # Every image the script knows how to rewrite, not just the ones this run
    # compares. `--bases python` narrows the comparison to the python
    # templates; it does not make a pytorch base unrecognisable.
    known_images = set(BASE_IMAGE_MAP.values())

    wrong = []
    unknown = []
    checked = 0
    # Non-recursive: nested service templates (e.g. subdir/Dockerfile-svc) are
    # outside this rewriter's reach. The preflight refuses them at deploy time.
    for path in sorted(DOCKERFILES_DIR.glob("Dockerfile*")):
        for line in path.read_text().splitlines():
            match = _FROM_RE.match(line)
            if not match:
                continue
            image_spec = match.group("image")
            prefix, image = _split_image_spec(image_spec)[:2]
            if image not in image_names:
                # A base this script cannot place is skipped by the rewriter
                # too, so the retarget left it on whatever it named and the
                # comparison below would never see it. Passing over it in
                # silence is the failure this whole mode exists to end.
                if "a2rchi" in image and image not in known_images:
                    unknown.append(f"  {path.relative_to(PROJECT_ROOT)}: {image_spec}")
                continue
            checked += 1
            # With no --switch-source the run names no registry, so the prefix
            # already on the line is the expected one and only the tag is checked.
            expected = _build_image_spec(
                source_prefix if source_prefix is not None else prefix,
                image,
                options.tag,
            )
            if image_spec != expected:
                rel_path = path.relative_to(PROJECT_ROOT)
                wrong.append(f"  {rel_path}: {image_spec} (expected {expected})")

    if unknown:
        listed = "\n".join(unknown)
        raise SystemExit(
            "These service templates name a base image this script cannot place, "
            f"so the retarget skipped them and this check cannot vouch for "
            f"them:\n{listed}\n"
            f"Add the image to BASE_IMAGE_MAP, or point the template at "
            f"{' or '.join(sorted(known_images))}."
        )

    if not checked:
        raise SystemExit(
            f"--verify found no service template under {DOCKERFILES_DIR} that "
            f"references {' or '.join(sorted(image_names))}. A check that reads "
            "nothing passes without reading anything, so this is a failure."
        )

    if wrong:
        listed = "\n".join(wrong)
        raise SystemExit(
            "These service templates do not reference the base images this run "
            f"names:\n{listed}"
        )

    plural = "" if checked == 1 else "s"
    print(f"Verified {checked} base reference{plural} at {options.tag}.")


def update_base_tags(options: UpdateOptions) -> None:
    bases = _validated_bases(options.bases)

    # Non-recursive: nested service templates (e.g. subdir/Dockerfile-svc) are
    # outside this rewriter's reach. The preflight refuses them at deploy time.
    for path in sorted(DOCKERFILES_DIR.glob("Dockerfile*")):
        original = path.read_text()
        updated = original
        changed = False

        for base in bases:
            image_name = BASE_IMAGE_MAP[base]
            new_lines, file_changed = _rewrite_lines(
                updated.splitlines(keepends=True), image_name, options
            )
            updated = "".join(new_lines)
            changed = changed or file_changed

        if changed and updated != original:
            path.write_text(updated)
            rel_path = path.relative_to(PROJECT_ROOT)
            print(f"Updated {rel_path}")


def parse_args() -> UpdateOptions:
    parser = argparse.ArgumentParser(
        description="Point service Dockerfiles at the given base image tag."
    )
    parser.add_argument("--tag", help="Base image tag to reference, e.g. v1.2.3")
    parser.add_argument(
        "--orig-tag",
        help="Only update lines using this tag (use 'all' to match any)",
        default="latest",
    )
    parser.add_argument(
        "--switch-source",
        choices=sorted(SOURCE_PREFIXES),
        help="Switch base image registry/source",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Check the references instead of writing them: exit non-zero unless "
            "every service base reference already names --tag (and --switch-source, "
            "when given)"
        ),
    )
    parser.add_argument(
        "--digest",
        action="append",
        metavar="NAME=sha256:HEX",
        default=[],
        help=(
            "Pin a base image by digest instead of by tag, e.g. "
            "python=sha256:<64 hex>. Repeatable."
        ),
    )
    parser.add_argument(
        "--bases",
        choices=sorted(BASE_IMAGE_MAP),
        nargs="+",
        default=sorted(BASE_IMAGE_MAP),
        help="Base images to update",
    )
    args = parser.parse_args()
    orig_tag = args.orig_tag
    if orig_tag in ("all", ""):
        orig_tag = None

    if args.tag is not None and not _TAG_RE.fullmatch(args.tag):
        # Both workflow call sites pass `--tag "${{ ... }}"`, so an unexpanded
        # or malformed value is one bad job output away. Neither output form
        # survives one. Without `--digest` the reference becomes
        # `FROM <repo>:dev bad`, which is two FROM arguments and a parse error,
        # or for an empty value a bare `FROM <repo>` that resolves to `latest`.
        # With `--digest` the annotation stops matching this script's own
        # pattern, so it is orphaned and no later run can remove it.
        raise SystemExit(
            f"--tag is not a valid image tag: {args.tag!r}. A tag matches "
            "[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}; writing anything else leaves "
            "the templates unbuildable or the base image unpinned."
        )
    # Refuse what cannot be honoured rather than writing it out: both a bad
    # name and a bad digest produce a reference no runtime can pull, and both
    # would surface at build time in CI, far from the command that caused them.
    digests_by_image = {}
    for entry in args.digest:
        name, _, digest = entry.partition("=")
        if name not in BASE_IMAGE_MAP:
            raise SystemExit(
                f"Unknown --digest base name: {name} "
                f"(valid names: {', '.join(sorted(BASE_IMAGE_MAP))})"
            )
        if not _DIGEST_RE.fullmatch(digest):
            raise SystemExit(
                f"Malformed --digest value for {name}: {digest} "
                "(expected sha256:<64 hex characters>)"
            )
        if name not in args.bases:
            # `update_base_tags` only walks the bases in `--bases`, so this
            # digest could never be applied. Exiting zero having written
            # nothing is the silent partial failure this option exists to end.
            raise SystemExit(
                f"--digest names {name}, which --bases excludes "
                f"(selected: {', '.join(args.bases)})"
            )
        digests_by_image[BASE_IMAGE_MAP[name]] = digest

    if digests_by_image and args.tag is None:
        # A digest names no build. Without a tag to record above it, the pin
        # says nothing about which build the services are on, and
        # `test_service_templates_pin_one_explicit_base_tag` rejects it — so
        # the command would leave the repository failing CI. Say so here.
        raise SystemExit(
            "--digest needs --tag: the tag names the build the digest is, and "
            "is recorded above the reference. A digest alone records nothing."
        )

    if args.verify:
        if args.tag is None:
            raise SystemExit(
                "--verify needs --tag: without one there is no expected "
                "reference to check the templates against."
            )
        if digests_by_image:
            raise SystemExit(
                "--verify cannot take --digest. The release retarget writes a "
                "tag reference and drops any digest, so verifying a digest "
                "would check a reference the release never writes."
            )

    return UpdateOptions(
        tag=args.tag,
        orig_tag=orig_tag,
        switch_source=args.switch_source,
        bases=args.bases,
        digests_by_image=digests_by_image,
        verify=args.verify,
    )


def main() -> None:
    options = parse_args()
    if options.verify:
        verify_base_tags(options)
        return
    update_base_tags(options)


if __name__ == "__main__":
    main()
