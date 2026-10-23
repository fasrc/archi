#!/usr/bin/env python3
"""Merge scored judge bundles into one judged result per run (judge offload D8).

    python scripts/benchmarking/judge/merge.py --queue <dir>

The unit of a merge is one result JSON, because the leaderboard and the A/B
comparisons need every arm. For each result basename of the READY bundles in
the queue, the merge:

1. waits (writes nothing, exit 0) while an arm has no bundle or a bundle that is
   not ``SCORED``, or while ``<queue>/results/<basename>.json`` is not yet
   published; a ``FAILED`` arm writes nothing and makes the exit 1;
2. checks that each arm's ``judge_bundle.digest`` in the pending result, the
   bundle's ``rows_sha256`` and its ``judge_scores.json`` agree;
3. writes each arm's per-row scores and aggregates, ``judge_status: scored``,
   ``ragas_effective_settings``, ``ragas_judge_identity_effective`` and
   ``judge_execution``, and copies every other field unchanged;
4. rebuilds the A/B comparisons and the leaderboard with ``ResultHandler``,
   which withholds ranks and winners when the arms' judge identities differ
   (#618);
5. publishes ``<basename>.judged.json`` with a no-replace ``link`` (sorted keys,
   ``indent=4``, strict JSON), then writes ``MERGED`` in each bundle.

The pending file is never changed. A judged file that exists with the same bytes
is a no-op; with other bytes the merge changes nothing and exits 1. A merge that
was killed leaves only a ``.tmp.*`` file, which a later merge removes once it is
older than one hour.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.bin.service_benchmark import ResultHandler  # noqa: E402
from src.utils.benchmark_schema import json_safe  # noqa: E402
from src.utils.exclusive_link import link_exclusive  # noqa: E402
from src.utils.judge_scorer import SCORES_KEYS  # noqa: E402

TEMPORARY_MAX_AGE_SECONDS = 3600
# The temporary-file prefix of link_exclusive.
_TEMPORARY_PREFIX = ".tmp."
_NO_SNAPSHOT = "<not recorded in the pending result>"


class MergeError(Exception):
    """This result cannot be merged; the message says why."""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise MergeError(f"{path} does not load: {exc}") from exc


def _nan_for_null(value: Any) -> Any:
    """Back to the in-memory spelling: the run keeps an unscored cell as NaN."""
    return math.nan if value is None else value


def _rebuild_comparisons(
    records: List[Dict[str, Any]], primary_metric: str, snapshot_id: str
) -> Dict[str, Any]:
    """The A/B comparisons and the leaderboard of *records*, by ResultHandler."""
    saved = (
        ResultHandler.results,
        ResultHandler.ab_comparison,
        ResultHandler.ab_comparisons,
        ResultHandler.leaderboard,
        ResultHandler._corpus_snapshot_id,
    )
    try:
        ResultHandler.results = records
        ResultHandler.ab_comparison = {}
        ResultHandler.ab_comparisons = []
        ResultHandler.leaderboard = {}
        # The run's snapshot id, not a new one: a second merge must give the
        # same bytes.
        ResultHandler._corpus_snapshot_id = snapshot_id
        ResultHandler.build_comparisons(primary_metric)
        rebuilt: Dict[str, Any] = {}
        if ResultHandler.ab_comparison:
            rebuilt["ab_comparison"] = ResultHandler.ab_comparison
        if ResultHandler.ab_comparisons:
            rebuilt["ab_comparisons"] = ResultHandler.ab_comparisons
        if ResultHandler.leaderboard:
            rebuilt["leaderboard"] = ResultHandler.leaderboard
        return rebuilt
    finally:
        (
            ResultHandler.results,
            ResultHandler.ab_comparison,
            ResultHandler.ab_comparisons,
            ResultHandler.leaderboard,
            ResultHandler._corpus_snapshot_id,
        ) = saved


def merge_result(
    pending: Dict[str, Any], scores_by_arm: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    """The judged result: a copy of *pending* with each arm's judge fields filled."""
    judged = copy.deepcopy(pending)
    records = judged["benchmarking_results"]
    for index, (record, scores) in enumerate(zip(records, scores_by_arm)):
        questions = record.get("single_question_results") or {}
        for key, cells in (scores.get("scores") or {}).items():
            if key not in questions:
                raise MergeError(
                    f"arm {index}: the scores hold question key {key}, which the "
                    f"pending result does not have"
                )
            for metric, value in cells.items():
                questions[key][metric] = _nan_for_null(value)
        totals = record.get("total_results")
        if not isinstance(totals, dict):
            totals = record["total_results"] = {}
        for name, value in (scores.get("aggregates") or {}).items():
            totals[name] = _nan_for_null(value)
        record["judge_status"] = "scored"
        record["ragas_effective_settings"] = scores["ragas_effective_settings"]
        record["ragas_judge_identity_effective"] = scores[
            "ragas_judge_identity_effective"
        ]
        record["judge_execution"] = scores["judge_execution"]
    for name in ("ab_comparison", "ab_comparisons", "leaderboard"):
        judged.pop(name, None)
    if len(records) >= 2:
        primary = (pending.get("leaderboard") or {}).get("primary_metric")
        snapshot = (pending.get("metadata") or {}).get("corpus_snapshot_id")
        judged.update(
            _rebuild_comparisons(
                records, primary or "faithfulness", snapshot or _NO_SNAPSHOT
            )
        )
    return judged


def judged_bytes(judged: Dict[str, Any]) -> bytes:
    """The ``dump`` format (``json_safe``, ``indent=4``, strict) with sorted keys."""
    return json.dumps(
        json_safe(judged), indent=4, allow_nan=False, sort_keys=True
    ).encode("utf-8")


def publish_judged(path: Path, data: bytes) -> str:
    """``written``, ``same`` (the file holds *data*) or ``conflict``; never replaces."""
    if link_exclusive(path, data):
        return "written"
    try:
        existing: Optional[bytes] = Path(path).read_bytes()
    except OSError:
        existing = None
    return "same" if existing == data else "conflict"


def remove_stale_temporaries(
    folder: Path, *, now: Callable[[], float] = time.time
) -> None:
    """Remove ``.tmp.*`` files older than one hour; a running merge's file is newer."""
    for path in Path(folder).glob(f"{_TEMPORARY_PREFIX}*"):
        try:
            if now() - path.stat().st_mtime > TEMPORARY_MAX_AGE_SECONDS:
                path.unlink()
        except FileNotFoundError:
            continue


def _group_bundles(queue: Path) -> Dict[str, Dict[str, Any]]:
    """Result basename -> {"count": arm_count, "arms": {arm_index: folder}}."""
    groups: Dict[str, Dict[str, Any]] = {}
    for folder in sorted(p for p in Path(queue).iterdir() if p.is_dir()):
        if not (folder / "READY").is_file():
            continue
        try:
            manifest = json.loads((folder / "manifest.json").read_bytes())
        except (OSError, ValueError):
            continue
        group = groups.setdefault(
            manifest.get("result_basename"),
            {"count": manifest.get("arm_count"), "arms": {}},
        )
        group["arms"][manifest.get("arm_index")] = folder
    return groups


def merge_one(queue: Path, basename: str, group: Dict[str, Any]) -> int:
    """Merge one result; 0 when merged or still waiting, 1 on any refusal."""
    count, arms = group["count"], group["arms"]
    failed = [i for i in sorted(arms) if (arms[i] / "FAILED").exists()]
    if failed:
        for index in failed:
            reason = (arms[index] / "FAILED").read_text().strip()
            print(
                f"merge: {basename}: arm {index} is FAILED: {reason}", file=sys.stderr
            )
        return 1
    waiting = [
        i for i in range(count) if i not in arms or not (arms[i] / "SCORED").exists()
    ]
    if waiting:
        names = ", ".join(f"arm {i}" for i in waiting)
        print(f"merge: {basename}: waits for {names} to be SCORED")
        return 0
    results = Path(queue) / "results"
    pending_path = results / f"{basename}.json"
    if not pending_path.is_file():
        print(f"merge: {basename}: the pending result is not yet published")
        return 0
    try:
        pending = _load_json(pending_path)
        records = pending.get("benchmarking_results")
        if not isinstance(records, list) or len(records) != count:
            found = len(records) if isinstance(records, list) else None
            raise MergeError(
                f"the pending result has {found} arms, but the bundles say "
                f"arm_count {count}"
            )
        scores_by_arm = []
        for index in range(count):
            manifest = _load_json(arms[index] / "manifest.json")
            scores = _load_json(arms[index] / "judge_scores.json")
            missing = [
                name
                for name in SCORES_KEYS
                if not isinstance(scores, dict)
                or not isinstance(scores.get(name), dict)
            ]
            if missing:
                raise MergeError(
                    f"arm {index}: judge_scores.json lacks {', '.join(missing)}"
                )
            recorded = (records[index].get("judge_bundle") or {}).get("digest")
            bundle_digest = manifest.get("rows_sha256")
            if not recorded == bundle_digest == scores.get("rows_sha256"):
                raise MergeError(
                    f"arm {index}: the pending result records bundle digest "
                    f"{recorded}, the bundle has {bundle_digest}, and its scores "
                    f"have {scores.get('rows_sha256')}"
                )
            scores_by_arm.append(scores)
        data = judged_bytes(merge_result(pending, scores_by_arm))
        json.loads(data)
    except MergeError as exc:
        print(f"merge: {basename}: {exc}", file=sys.stderr)
        return 1

    remove_stale_temporaries(results)
    judged_path = results / f"{basename}.judged.json"
    outcome = publish_judged(judged_path, data)
    if outcome == "conflict":
        print(
            f"merge: {basename}: {judged_path} exists with bytes that differ from "
            f"this merge; nothing replaced",
            file=sys.stderr,
        )
        return 1
    for index in range(count):
        link_exclusive(arms[index] / "MERGED", b"")
    print(f"merge: {basename}: {outcome} {judged_path}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge scored judge bundles into judged results."
    )
    parser.add_argument("--queue", required=True, type=Path)
    args = parser.parse_args(argv)
    status = 0
    for basename, group in sorted(_group_bundles(args.queue).items()):
        status = max(status, merge_one(args.queue, basename, group))
    return status


if __name__ == "__main__":
    sys.exit(main())
