"""Paired exact McNemar tests for per-question binary outcomes (plan §5).

The rung-0 sweep's verdicts are count-type and paired per question:

- **source** — relative source hit (any declared source matched), over rows that
  declare sources, are clean in both arms, and declare the same canonical
  sources in both (a bank edit between artifacts would otherwise pair hits
  against different gold targets);
- **completion** — status ``ok`` versus not, over the common question set.

``b`` counts questions where the baseline succeeds and the arm fails, ``c`` the
reverse, so the exact p-value comes with a direction. ``mcnemar_exact`` is
ported unchanged from ``fasrc/archi-bench-out``
``feature_matrix/figures/extract_figure_data.py:67-74``.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence

from src.utils.benchmark_provenance import canonical_source_url


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for discordant counts b and c."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2**n
    return min(1.0, 2 * p)


def holm_adjust(pvalues: Sequence[float]) -> List[float]:
    """Holm step-down adjusted p-values, in the input order."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[index]))
        adjusted[index] = running
    return adjusted


def paired_binary(base: Mapping[str, bool], arm: Mapping[str, bool]) -> Dict[str, Any]:
    """Exact McNemar over the questions both mappings carry."""
    common = [key for key in base if key in arm]
    b = sum(1 for key in common if base[key] and not arm[key])
    c = sum(1 for key in common if arm[key] and not base[key])
    if c > b:
        direction = "arm better"
    elif b > c:
        direction = "arm worse"
    else:
        direction = "no difference"
    return {
        "pairs": len(common),
        "b": b,
        "c": c,
        "n": b + c,
        "p": mcnemar_exact(b, c),
        "direction": direction,
    }


def _clean(row: Mapping[str, Any]) -> bool:
    return row.get("status", "ok") == "ok"


def _declared(row: Mapping[str, Any]) -> List[str]:
    return [
        canonical_source_url(value)
        for entry in (row.get("reference_sources_metadata") or [])
        for key, value in entry.items()
        if key != "matched"
    ]


def _relative_hit(row: Mapping[str, Any]) -> bool:
    return any(
        entry.get("matched") for entry in row.get("reference_sources_metadata") or []
    )


def source_test(
    base_rows: Mapping[str, Mapping[str, Any]],
    arm_rows: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Paired relative source hits; lists questions whose gold sources differ."""
    base: Dict[str, bool] = {}
    arm: Dict[str, bool] = {}
    differ: List[str] = []
    for key, base_row in base_rows.items():
        arm_row = arm_rows.get(key)
        if arm_row is None or not _declared(base_row):
            continue
        if not (_clean(base_row) and _clean(arm_row)):
            continue
        if _declared(base_row) != _declared(arm_row):
            differ.append(key)
            continue
        base[key] = _relative_hit(base_row)
        arm[key] = _relative_hit(arm_row)
    result = paired_binary(base, arm)
    result["sources_differ"] = differ
    return result


def completion_test(
    base_rows: Mapping[str, Mapping[str, Any]],
    arm_rows: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Paired completion (status ``ok``) over the common question set."""
    base = {key: _clean(row) for key, row in base_rows.items()}
    arm = {key: _clean(row) for key, row in arm_rows.items()}
    return paired_binary(base, arm)
