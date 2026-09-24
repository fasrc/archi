"""Per-category slice, map rule and QA join rule for ``compare_runs.py`` (W6).

Reads the per-arm fields ``record-category-map-digest`` writes and applies the
rules decided on #524, #525 and #538 (plan
``docs/docs/proposals/categories-action-plan.md`` §6.1):

- **The snapshot is bound to its run.** An arm's slice reads its
  ``category_map_file`` only when the file exists, the corpus fingerprint is
  recorded and stable at both endpoints, the category map did not change
  between them, and the file's sha256 equals ``category_map_sha256_end``. A pair
  also needs equal corpus fingerprints, whatever ``--corpus-differs-by-design``
  says for the other sections.
- **Counting** comes from ``category_attribution``, the same rules the preflight
  census uses.
- **Map rule** (#538 rule 3): a map mismatch between arms — any of the four
  readings missing or unavailable, a start differing from its end, or two
  different end digests — voids a comparison with an arm that routes on
  ``category`` (r0a), and otherwise drops only the slice, unless either side
  called ``search_metadata_index``, which voids the comparison too.
- **QA join** (#538 rule 1, plan W8): for an arm that records a map digest, a QA
  run joins only when its own start and end readings equal that digest; for an
  arm that records ``agent_md_sha256``, only when the QA run used that prompt.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from scripts.benchmarking.category_attribution import (
    attribute,
    metric_power,
    metric_rows,
    url_categories,
)
from src.utils.benchmark_provenance import canonical_source_url

UNAVAILABLE = "<unavailable:"
METADATA_TOOL = "search_metadata_index"
MIN_ARTICLES = 3
POWER_METRICS = ("completion", "source")


def _usable(reading: Any) -> bool:
    return (
        isinstance(reading, str)
        and bool(reading)
        and not reading.startswith(UNAVAILABLE)
    )


def _unescape(field: str) -> str:
    return (
        field.replace("%0D", "\r")
        .replace("%0A", "\n")
        .replace("%09", "\t")
        .replace("%25", "%")
    )


def parse_snapshot(text: str) -> Dict[str, Optional[str]]:
    """URL -> category from a ``_category_map_<N>.tsv`` snapshot."""
    pairs: List[Tuple[str, Optional[str]]] = []
    for line in text.split("\n") if text else []:
        url, _, category = line.partition("\t")
        pairs.append((_unescape(url), _unescape(category) or None))
    return url_categories(pairs)


@dataclass
class SnapshotCheck:
    reason: Optional[str]
    category_of: Optional[Dict[str, Optional[str]]] = None


def snapshot_check(entry: Mapping[str, Any], artifact_dir: Path) -> SnapshotCheck:
    """The four per-arm checks, in order; the first failure is named."""
    name = entry.get("category_map_file")
    if "category_map_sha256_end" not in entry or not name:
        return SnapshotCheck("no snapshot")
    path = Path(artifact_dir) / str(name)
    if not path.is_file():
        return SnapshotCheck("no snapshot")
    if not _usable(entry.get("corpus_fingerprint")) or (
        entry.get("corpus_unchanged_at_endpoints") is not True
    ):
        return SnapshotCheck("corpus not stable at the endpoints")
    unchanged = entry.get("category_map_unchanged_at_endpoints")
    if unchanged is False:
        return SnapshotCheck("map changed between endpoints")
    if unchanged is not True:
        return SnapshotCheck("a map reading failed")
    body = path.read_bytes()
    if f"sha256:{hashlib.sha256(body).hexdigest()}" != entry.get(
        "category_map_sha256_end"
    ):
        return SnapshotCheck("snapshot does not match the end digest")
    return SnapshotCheck(None, parse_snapshot(body.decode("utf-8")))


def pair_slice_reason(
    base: Mapping[str, Any],
    arm: Mapping[str, Any],
    base_dir: Path,
    arm_dir: Path,
) -> Optional[str]:
    """Why a pair gets no per-category comparison, or ``None`` when it does."""
    for side, entry, directory in (("baseline", base, base_dir), ("arm", arm, arm_dir)):
        reason = snapshot_check(entry, directory).reason
        if reason:
            return f"{side}: {reason}"
    if base.get("corpus_fingerprint") != arm.get("corpus_fingerprint"):
        return "arms scored different corpora"
    return None


# --- the per-category table ----------------------------------------------------------


def _sources(row: Mapping[str, Any]) -> List[str]:
    return [
        canonical_source_url(value)
        for entry in (row.get("reference_sources_metadata") or [])
        for key, value in entry.items()
        if key != "matched"
    ]


def _clean(row: Mapping[str, Any]) -> bool:
    return row.get("status", "ok") == "ok"


def _relative_hit(row: Mapping[str, Any]) -> bool:
    return any(
        entry.get("matched") for entry in row.get("reference_sources_metadata") or []
    )


def category_table(
    rows: Mapping[str, Mapping[str, Any]],
    category_of: Mapping[str, Optional[str]],
) -> Dict[str, Any]:
    """Per category: owned gold rows, coverage, source accuracy, completion, power."""
    result = attribute({key: _sources(row) for key, row in rows.items()}, category_of)
    categories: Dict[str, Dict[str, Any]] = {}
    for category in sorted(set(result.gold_rows) | set(result.coverage)):
        owned = result.gold_rows.get(category, [])
        source_rows = [
            key for key in metric_rows(result, category, "source") if _clean(rows[key])
        ]
        hits = sum(1 for key in source_rows if _relative_hit(rows[key]))
        power = {
            metric: metric_power(result, category, metric) for metric in POWER_METRICS
        }
        categories[category] = {
            "gold_rows": len(owned),
            "coverage": len(result.coverage.get(category, ())),
            "completion": (
                sum(1 for key in owned if _clean(rows[key])) / len(owned)
                if owned
                else None
            ),
            "source_rows": len(source_rows),
            "source_accuracy": hits / len(source_rows) if source_rows else None,
            "power": power,
            "underpowered": [m for m in POWER_METRICS if power[m] < MIN_ARTICLES],
        }
    return {
        "categories": categories,
        "cross_category": result.cross_category,
        "uncategorized": result.uncategorized,
        "unresolved": [list(pair) for pair in result.unresolved],
    }


# --- traces and the map rule ---------------------------------------------------------


def called_metadata_search(
    rows: Mapping[str, Mapping[str, Any]],
    qa_answers: Sequence[Mapping[str, Any]],
) -> bool:
    """Whether any benchmark or joined-QA trace called ``search_metadata_index``."""
    for row in rows.values():
        for message in row.get("messages") or []:
            if (
                message.get("type") == "tool_call"
                and message.get("tool_name") == METADATA_TOOL
            ):
                return True
    for answer in qa_answers:
        for call in answer.get("tool_calls") or []:
            if call.get("name") == METADATA_TOOL:
                return True
    return False


def _maps_mismatch(base: Mapping[str, Any], arm: Mapping[str, Any]) -> bool:
    readings = [
        entry.get(key)
        for entry in (base, arm)
        for key in ("category_map_sha256_start", "category_map_sha256_end")
    ]
    if not all(_usable(reading) for reading in readings):
        return True
    base_start, base_end, arm_start, arm_end = readings
    return base_start != base_end or arm_start != arm_end or base_end != arm_end


def map_rule(
    base: Mapping[str, Any],
    arm: Mapping[str, Any],
    *,
    routes_on_category: bool,
    traced: bool,
) -> Tuple[str, Optional[str]]:
    """``("ok" | "slice-only" | "void", reason)`` for one baseline/arm pair."""
    key = "category_map_sha256_end"
    if key not in base and key not in arm:
        return "ok", None
    if not _maps_mismatch(base, arm):
        return "ok", None
    if routes_on_category:
        return "void", (
            "the category map differs or was not observed, and this arm routes "
            "on category"
        )
    if traced:
        return "void", (
            f"the category maps differ and a trace shows {METADATA_TOOL}, "
            "the tool that reads the map"
        )
    return "slice-only", "the category maps differ; the per-category slice is dropped"


# --- QA join -------------------------------------------------------------------------


def qa_join_reason(
    arm: Mapping[str, Any],
    readings: Optional[Mapping[str, Any]],
    qa_spec_sha256: Optional[str],
) -> Optional[str]:
    """Why a QA run may not join *arm*, or ``None`` when it may."""
    if "category_map_sha256_end" in arm:
        arm_end = arm.get("category_map_sha256_end")
        if not _usable(arm_end):
            return "the arm's category-map reading failed"
        if readings is None:
            return "no map readings for this QA run"
        start, end = readings.get("start"), readings.get("end")
        if not (_usable(start) and _usable(end)):
            return "a QA map reading failed"
        if start != arm_end:
            return "QA run started on a different map than the arm ended on"
        if end != start:
            return "map changed during the QA run"
    arm_spec = arm.get("agent_md_sha256")
    if _usable(arm_spec) and qa_spec_sha256 != arm_spec:
        return (
            "QA run used a different agent spec "
            f"(arm {arm_spec}, QA run {qa_spec_sha256})"
        )
    return None
