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

_TRAILING_COMMENT_RE = re.compile(r"(?P<rest>.*?)(?P<comment>\s+#.*)$")


def _split_trailing_comment(suffix: str) -> Tuple[str, str]:
    """Split a FROM line's trailing text into (rest, comment).

    The comment starts at the first "#" that follows whitespace. The reference
    itself can never contain it: the `\\S+` image group stops at the space
    before it. `rest` holds anything else the line carries, such as a build
    stage name, and always survives a rewrite.
    """
    match = _TRAILING_COMMENT_RE.match(suffix)
    if not match:
        return suffix, ""
    return match.group("rest"), match.group("comment")


def _split_line_ending(line: str) -> Tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def _update_line(line: str, base_name: str, options: UpdateOptions) -> Tuple[str, bool]:
    core, newline = _split_line_ending(line)
    stripped = core.lstrip()
    if not stripped.startswith("FROM "):
        return line, False

    match = re.match(
        r"(?P<intro>\s*FROM\s+(?:--platform=\S+\s+)?)(?P<image>\S+)(?P<suffix>.*)", core
    )
    if not match:
        return line, False

    intro, image_spec, suffix = (
        match.group("intro"),
        match.group("image"),
        match.group("suffix"),
    )
    rest, comment = _split_trailing_comment(suffix)
    prefix, image, current_tag, current_digest = _split_image_spec(image_spec)

    if image != base_name:
        return line, False

    if options.orig_tag is not None and current_tag != options.orig_tag:
        return line, False

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
    # the build its comment names.
    keeping_digest = (
        options.tag is None
        and current_digest is not None
        and target_digest == current_digest
    )

    if target_digest is not None:
        # A digest says nothing about which build it is, so the tag goes in a
        # comment beside it. No tag given, no comment.
        updated_spec = _build_image_spec(target_prefix, image, None, target_digest)
        if keeping_digest:
            # The digest did not move, so its annotation still tells the truth.
            updated_comment = comment
        elif options.tag:
            # 13 of the 15 service templates end this line with a stray space.
            # An annotation cannot be appended to such a line and read back —
            # the split cannot tell the stray space from the comment's own
            # separator — so the trailing whitespace is normalized here, once.
            rest = rest.rstrip()
            updated_comment = f"  # {options.tag}"
        else:
            updated_comment = ""
    else:
        updated_spec = _build_image_spec(target_prefix, image, target_tag)
        # The annotation names the build the digest is. Writing a tag in place
        # of a digest leaves it naming a build the line no longer references.
        updated_comment = "" if current_digest is not None else comment

    # Compare the whole line, not just the reference: re-pinning the same
    # digest under a new tag changes only the annotation, and that is a change.
    updated_line = f"{intro}{updated_spec}{rest}{updated_comment}{newline}"
    if updated_line == line:
        return line, False

    return updated_line, True


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
            new_lines = []
            file_changed = False
            for line in updated.splitlines(keepends=True):
                new_line, line_changed = _update_line(line, image_name, options)
                new_lines.append(new_line)
                file_changed = file_changed or line_changed
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
