#!/usr/bin/env python3
"""Publish complete judge bundles to the judge queue (judge offload design D11).

    python scripts/benchmarking/judge/publish.py --out-dir <dir> --queue <dir> \
        [--bundle-dir judge_bundles]

Each step is idempotent, so a timer can run the script again and again:

1. Bundles. Each folder under ``<out-dir>/<bundle-dir>/`` that has
   ``BUNDLE_COMPLETE`` is copied to ``<queue>/<folder>/``, checked against its
   manifest, and gets ``READY`` last. No queue file is ever replaced. A folder
   with ``READY`` and the same rows digest is a skip.
2. Pending result. When ``<out-dir>/<basename>.RESULT_COMPLETE`` exists, the
   result JSON loads, and every arm has a queued bundle whose digest the result
   records as pending, the JSON is copied to ``<queue>/results/``.
3. Judged copy-back. ``<queue>/results/<basename>.judged.json`` is copied back
   to ``<out-dir>/`` once every arm folder of that result has ``MERGED``.

A conflict is reported on stderr; the script goes on with the other bundles and
exits 1 at the end. With no conflict it exits 0, also when there is nothing to
do. ``--queue`` is required: the queue location is an operator decision (#729).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.exclusive_link import link_exclusive  # noqa: E402
from src.utils.judge_bundle import DEFAULT_BUNDLE_DIR, judge_status_of  # noqa: E402

_COPIED_FILES = ("rows.jsonl", "manifest.json")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _copy_file(src: Path, dst: Path) -> bool:
    """Create *dst* with the bytes of *src*; never replace an existing *dst*.

    Two publishers can meet at one queue folder before ``READY`` exists, so no
    queue file is ever replaced: a file that one of them checked stays what it
    checked. A *dst* left by an earlier run must already hold the same bytes.
    Returns False when it holds other bytes.
    """
    data = src.read_bytes()
    if link_exclusive(dst, data):
        return True
    return dst.read_bytes() == data


def _load_json(path: Path) -> Optional[Any]:
    """The JSON in *path*, or None when it cannot be read or does not load."""
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


class _Publisher:
    def __init__(self, out_dir: Path, queue: Path) -> None:
        self.out_dir = out_dir
        self.queue = queue
        self.conflicts: List[str] = []
        # result basename -> arm_count, for every complete bundle read in step 1
        self.arm_counts: Dict[str, int] = {}
        # result basename -> {arm_index: rows_sha256} of the bundles in the queue
        self.queued: Dict[str, Dict[int, str]] = {}

    def conflict(self, message: str) -> None:
        self.conflicts.append(message)
        print(f"publish: conflict: {message}", file=sys.stderr)

    def publish_bundle(self, folder: Path) -> None:
        """Step 1 for one bundle folder."""
        if not (folder / "BUNDLE_COMPLETE").is_file():
            return
        try:
            manifest_bytes = (folder / "manifest.json").read_bytes()
            manifest = json.loads(manifest_bytes)
        except (OSError, ValueError) as exc:
            self.conflict(f"{folder}: manifest.json does not load: {exc}")
            return
        basename = manifest.get("result_basename")
        arm_index = manifest.get("arm_index")
        arm_count = manifest.get("arm_count")
        digest = manifest.get("rows_sha256")
        if folder.name != f"{basename}-arm{arm_index}":
            self.conflict(
                f"{folder}: the folder name is not {basename}-arm{arm_index}, "
                f"as its manifest says"
            )
            return
        if (
            not isinstance(arm_index, int)
            or not isinstance(arm_count, int)
            or not 0 <= arm_index < arm_count
        ):
            self.conflict(
                f"{folder}: arm_index {arm_index!r} is not inside arm_count "
                f"{arm_count!r}"
            )
            return
        known = self.arm_counts.setdefault(basename, arm_count)
        if known != arm_count:
            self.conflict(
                f"{folder}: arm_count {arm_count} differs from {known} in another "
                f"bundle of {basename}"
            )
            return

        queued = self.queue / folder.name
        if not (queued / "READY").exists():
            queued.mkdir(parents=True, exist_ok=True)
            for name in _COPIED_FILES:
                if not _copy_file(folder / name, queued / name):
                    self.conflict(
                        f"{queued / name}: exists with bytes that differ from "
                        f"{folder / name}; nothing replaced, no READY"
                    )
                    return
            if (
                _sha256((queued / "rows.jsonl").read_bytes()) != digest
                or (queued / "manifest.json").read_bytes() != manifest_bytes
            ):
                self.conflict(
                    f"{queued}: the copy does not match {folder} and its "
                    f"rows_sha256 {digest}; no READY"
                )
                return
            if link_exclusive(queued / "READY", b""):
                print(f"publish: queued {folder.name}")
                self.queued.setdefault(basename, {})[arm_index] = digest
                return
            # Another publisher created READY first: check its rows below.

        queued_manifest = _load_json(queued / "manifest.json")
        queued_digest = (
            queued_manifest.get("rows_sha256")
            if isinstance(queued_manifest, dict)
            else None
        )
        if queued_digest != digest:
            self.conflict(
                f"{queued}: READY holds rows {queued_digest}, but {folder} has "
                f"{digest}; nothing changed"
            )
            return
        self.queued.setdefault(basename, {})[arm_index] = digest

    def publish_result(self, basename: str) -> None:
        """Step 2 for one result basename."""
        result_path = self.out_dir / f"{basename}.json"
        if not (self.out_dir / f"{basename}.RESULT_COMPLETE").exists():
            return
        try:
            data = result_path.read_bytes()
            result = json.loads(data)
        except (OSError, ValueError):
            return  # a later run retries
        arm_count = self.arm_counts[basename]
        queued = self.queued.get(basename, {})
        missing = [i for i in range(arm_count) if i not in queued]
        if missing:
            self.conflict(
                f"{result_path}: RESULT_COMPLETE exists, but the queued bundles of "
                f"these arms are missing: " + ", ".join(f"arm {i}" for i in missing)
            )
            return
        arms = result.get("benchmarking_results") if isinstance(result, dict) else None
        if not isinstance(arms, list) or len(arms) != arm_count:
            found = len(arms) if isinstance(arms, list) else None
            self.conflict(
                f"{result_path}: benchmarking_results has {found} arms, but the "
                f"bundles say arm_count {arm_count}"
            )
            return
        for arm_index in range(arm_count):
            entry = arms[arm_index]
            if not isinstance(entry, dict) or judge_status_of(entry) != "pending":
                self.conflict(
                    f"{result_path}: arm {arm_index} does not have judge_status "
                    f"pending"
                )
                return
            recorded = (entry.get("judge_bundle") or {}).get("digest")
            if recorded != queued[arm_index]:
                self.conflict(
                    f"{result_path}: arm {arm_index} records bundle digest "
                    f"{recorded}, but its bundle has {queued[arm_index]}"
                )
                return
        self._create_or_compare(
            self.queue / "results" / f"{basename}.json", data, result_path
        )

    def copy_back(self, basename: str) -> None:
        """Step 3 for one result basename."""
        judged = self.queue / "results" / f"{basename}.judged.json"
        if not judged.is_file():
            return
        arm_count = self.arm_counts[basename]
        if not all(
            (self.queue / f"{basename}-arm{i}" / "MERGED").exists()
            for i in range(arm_count)
        ):
            return
        self._create_or_compare(
            self.out_dir / f"{basename}.judged.json", judged.read_bytes(), judged
        )

    def _create_or_compare(self, path: Path, data: bytes, source: Path) -> None:
        """Create *path* with *data*; an existing *path* must hold the same bytes."""
        path.parent.mkdir(parents=True, exist_ok=True)
        if link_exclusive(path, data):
            print(f"publish: wrote {path}")
            return
        try:
            existing: Optional[bytes] = path.read_bytes()
        except OSError:
            existing = None
        if existing != data:
            self.conflict(
                f"{path}: exists with bytes that differ from {source}; nothing changed"
            )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish complete judge bundles to the judge queue."
    )
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--bundle-dir", default=DEFAULT_BUNDLE_DIR)
    args = parser.parse_args(argv)

    publisher = _Publisher(args.out_dir, args.queue)
    root = args.out_dir / args.bundle_dir
    if root.is_dir():
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            publisher.publish_bundle(folder)
    for basename in sorted(publisher.arm_counts):
        publisher.publish_result(basename)
        publisher.copy_back(basename)
    return 1 if publisher.conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
