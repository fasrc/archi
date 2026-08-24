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


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")

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

    match = re.match(
        r"(?P<intro>\s*FROM\s+(?:--platform=\S+\s+)?)(?P<image>\S+)(?P<suffix>.*)", core
    )
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
    # tag is given to name — whether the caller left the digest unnamed or
    # named the one already there. A digest that has not moved still points at
    # the build its annotation names.
    keeping_digest = (
        options.tag is None
        and current_digest is not None
        and target_digest == current_digest
    )

    if target_digest is not None:
        # A digest says nothing about which build it is, so the tag from
        # `--tag` is recorded above it. No tag given, no annotation.
        updated_spec = _build_image_spec(target_prefix, image, None, target_digest)
        if keeping_digest:
            updated_annotation = current_annotation
        elif options.tag:
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
            updated_annotation = None
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


def update_base_tags(options: UpdateOptions) -> None:
    bases = list(options.bases)
    invalid = set(bases) - set(BASE_IMAGE_MAP)
    if invalid:
        raise SystemExit(f"Unknown base types requested: {', '.join(sorted(invalid))}")

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

    return UpdateOptions(
        tag=args.tag,
        orig_tag=orig_tag,
        switch_source=args.switch_source,
        bases=args.bases,
        digests_by_image=digests_by_image,
    )


def main() -> None:
    options = parse_args()
    update_base_tags(options)


if __name__ == "__main__":
    main()
