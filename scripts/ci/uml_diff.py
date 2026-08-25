#!/usr/bin/env python3
"""Build a Mermaid class-diagram diff between two pyreverse ``.mmd`` files.

The uml-diff workflow runs ``pyreverse -o mmd -f ALL`` on the Python files a
PR changed, once per side (base and head), and hands both files to this
script. The script emits one Markdown file with a single ``classDiagram``
that tells the diff story visually:

* green class (``:::added``) — the class exists only on the head side
* red class (``:::removed``) — the class exists only on the base side
* yellow class (``:::modified``) — same class, different members; a new
  member gets a leading ``➕``, a removed member a leading ``➖`` (leading,
  because Mermaid renders text after ``method()`` as a return type)
* grey name-only class (``:::unchanged``) — context, members hidden

Noise filters: unchanged dunder members are hidden (``__init__`` stays),
and stdlib classes never appear because pyreverse omits them by default.

A missing input file is a valid empty side: a PR that only adds Python
files has no base-side model at all, so the whole head renders as added.

Exit codes: 0 — comment file written; 3 — no structural change, the caller
skips the comment; 2 — bad arguments (argparse).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

MARKER = "<!-- archi-uml-diff -->"
MAX_MERMAID_CHARS = 45000  # GitHub rejects Mermaid blocks near 50k chars.

CLASSDEFS = (
    "classDef added fill:#dafbe1,stroke:#1a7f37,color:#1f2328",
    "classDef removed fill:#ffebe9,stroke:#cf222e,color:#1f2328",
    "classDef modified fill:#fff8c5,stroke:#9a6700,color:#1f2328",
    "classDef unchanged fill:#f6f8fa,stroke:#d0d7de,color:#656d76",
)

LEGEND = (
    "🟩 added · 🟥 removed · 🟨 modified · ⬜ unchanged context — "
    "➕ new member · ➖ removed member"
)

_CLASS_OPEN = re.compile(r"^\s*class\s+([^\s{]+)\s*\{\s*$")
_CLASS_BARE = re.compile(r"^\s*class\s+([^\s{]+)\s*$")
_RELATION = re.compile(r"^\s*(\S+)\s+((?:--|\.\.)[|*o>]*)\s+(\S+)\s*(?::.*)?$")

Relation = tuple[str, str, str]


@dataclass
class Model:
    """Classes (name -> raw member lines, head order) plus relation arrows."""

    classes: dict[str, list[str]] = field(default_factory=dict)
    relations: set[Relation] = field(default_factory=set)


@dataclass
class ClassChange:
    """Member-level delta for a class present on both sides."""

    added_members: list[str]
    removed_members: list[str]
    kept_members: list[str]


@dataclass
class Diff:
    added: dict[str, list[str]]
    removed: dict[str, list[str]]
    modified: dict[str, ClassChange]
    unchanged: list[str]
    added_relations: set[Relation]
    removed_relations: set[Relation]
    kept_relations: set[Relation]

    @property
    def is_empty(self) -> bool:
        return not (
            self.added
            or self.removed
            or self.modified
            or self.added_relations
            or self.removed_relations
        )


def parse_mmd(text: str) -> Model:
    """Parse pyreverse Mermaid output into a Model.

    Member lines stay raw (escapes such as ``\\_\\_init\\_\\_`` included) so
    the emitter can re-use them verbatim; both sides escape identically, so
    raw-string comparison is sound.
    """
    model = Model()
    current: str | None = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line == "classDiagram":
            continue
        if current is not None:
            if line == "}":
                current = None
            else:
                model.classes[current].append(line)
            continue
        opened = _CLASS_OPEN.match(raw)
        if opened:
            current = opened.group(1)
            model.classes.setdefault(current, [])
            continue
        bare = _CLASS_BARE.match(raw)
        if bare:
            model.classes.setdefault(bare.group(1), [])
            continue
        rel = _RELATION.match(raw)
        if rel:
            model.relations.add((rel.group(1), rel.group(2), rel.group(3)))
    return model


def diff_models(base: Model, head: Model) -> Diff:
    base_names, head_names = set(base.classes), set(head.classes)
    added = {n: head.classes[n] for n in sorted(head_names - base_names)}
    removed = {n: base.classes[n] for n in sorted(base_names - head_names)}
    modified: dict[str, ClassChange] = {}
    unchanged: list[str] = []
    for name in sorted(base_names & head_names):
        base_set = set(base.classes[name])
        head_set = set(head.classes[name])
        if base_set == head_set:
            unchanged.append(name)
            continue
        modified[name] = ClassChange(
            added_members=[m for m in head.classes[name] if m not in base_set],
            removed_members=[m for m in base.classes[name] if m not in head_set],
            kept_members=[m for m in head.classes[name] if m in base_set],
        )
    return Diff(
        added=added,
        removed=removed,
        modified=modified,
        unchanged=unchanged,
        added_relations=head.relations - base.relations,
        removed_relations=base.relations - head.relations,
        kept_relations=head.relations & base.relations,
    )


def _member_name(member: str) -> str:
    """Bare member name, with the Mermaid underscore escapes undone."""
    return member.replace("\\_", "_").split("(")[0].split(":")[0].strip()


def _keep_unchanged_member(member: str) -> bool:
    """Hide unchanged dunder members; ``__init__`` is signal, so it stays."""
    name = _member_name(member)
    if not name.startswith("__"):
        return True
    return name.startswith("__init__")


def _class_block(name: str, style: str, members: list[str]) -> list[str]:
    if not members:
        return [f"  class {name}:::{style}"]
    lines = [f"  class {name}:::{style} {{"]
    lines.extend(f"    {member}" for member in members)
    lines.append("  }")
    return lines


def _mermaid_block(diff: Diff, include_context: bool) -> str:
    lines = ["classDiagram"]
    for name, members in diff.added.items():
        lines.extend(_class_block(name, "added", members))
    for name, change in diff.modified.items():
        members = [m for m in change.kept_members if _keep_unchanged_member(m)]
        members.extend(f"➕{m}" for m in change.added_members)
        members.extend(f"➖{m}" for m in change.removed_members)
        lines.extend(_class_block(name, "modified", members))
    for name, members in diff.removed.items():
        lines.extend(_class_block(name, "removed", members))
    if include_context:
        for name in diff.unchanged:
            lines.extend(_class_block(name, "unchanged", []))
    rendered = set(diff.added) | set(diff.modified) | set(diff.removed)
    if include_context:
        rendered |= set(diff.unchanged)
    relations = diff.added_relations | diff.removed_relations | diff.kept_relations
    for left, arrow, right in sorted(relations):
        # An arrow to an undeclared name makes Mermaid invent an unstyled
        # node, so arrows into dropped context are dropped with it.
        if left in rendered and right in rendered:
            lines.append(f"  {left} {arrow} {right}")
    lines.extend(f"  {classdef}" for classdef in CLASSDEFS)
    return "\n".join(lines)


def _summary_table(diff: Diff) -> str:
    rows = ["| Class | Change | Members ➕ | Members ➖ |", "| --- | --- | --- | --- |"]
    for name, members in diff.added.items():
        rows.append(f"| {name} | added | {len(members)} | 0 |")
    for name, change in diff.modified.items():
        rows.append(
            f"| {name} | modified | {len(change.added_members)}"
            f" | {len(change.removed_members)} |"
        )
    for name, members in diff.removed.items():
        rows.append(f"| {name} | removed | 0 | {len(members)} |")
    return "\n".join(rows)


def render_markdown(diff: Diff, max_chars: int = MAX_MERMAID_CHARS) -> str | None:
    """Render the PR comment body, or None when there is nothing to show."""
    if diff.is_empty:
        return None
    counts = (
        f"{len(diff.added)} added · {len(diff.modified)} modified · "
        f"{len(diff.removed)} removed · {len(diff.unchanged)} unchanged"
    )
    note = ""
    block = _mermaid_block(diff, include_context=True)
    if len(block) > max_chars:
        block = _mermaid_block(diff, include_context=False)
        note = "_Unchanged context classes are hidden (size cap)._"
    if len(block) > max_chars:
        body = _summary_table(diff)
        note = "_The diagram exceeds GitHub's Mermaid size cap; summary table only._"
    else:
        body = f"```mermaid\n{block}\n```"
    parts = [
        MARKER,
        "### 📐 UML class diff",
        f"**Legend:** {LEGEND}",
        body,
    ]
    if note:
        parts.append(note)
    parts.append(
        f"<sub>{counts} — from `pyreverse` over the PR's changed Python files. "
        "Same-named classes from different modules merge into one node.</sub>"
    )
    return "\n\n".join(parts) + "\n"


def _read_model(path: str) -> Model:
    file = Path(path)
    if not file.is_file():
        return Model()
    return parse_mmd(file.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="classes_*.mmd of the base side")
    parser.add_argument("--head", required=True, help="classes_*.mmd of the head side")
    parser.add_argument("--out", required=True, help="Markdown file to write")
    args = parser.parse_args(argv)
    markdown = render_markdown(
        diff_models(_read_model(args.base), _read_model(args.head))
    )
    if markdown is None:
        print("uml-diff: no structural change", file=sys.stderr)
        return 3
    Path(args.out).write_text(markdown, encoding="utf-8")
    print(f"uml-diff: wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
