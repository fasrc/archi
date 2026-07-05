"""Question-bank dialect + RAGAS metric eligibility for the benchmark harness.

Pure, ragas-free helpers (unit-testable without the benchmark-only ragas dep,
which is absent from the unit-test env). They sit beside the run-status helpers
in ``benchmark_resilience`` but own a different axis: the *dialect/schema*
contract and per-metric *data-emptiness* eligibility, as opposed to
failed/degraded run-status resilience.

Dialect
    ``normalize_record`` / ``normalize_bank`` map the legacy authoring dialect
    (``question`` / ``answer`` / ``contexts``) onto ragas 0.3.5's modern schema
    (``user_input`` / ``reference`` / ``retrieved_contexts``), preserving archi
    extension fields (``sources`` / ``source_match_field`` / ``anchor_type`` /
    ``notes``). The single highest-risk mapping is ``answer -> reference`` (the
    ground truth), NEVER ``answer -> response`` (which is the agent's run-time
    answer).

Validation vs eligibility
    ``required_fields_for_modes`` is *schema* validation and is deliberately
    SEPARATE from metric eligibility: ``user_input`` is always required and
    SOURCES mode adds ``sources``, but RAGAS mode does NOT require ``reference``
    at load — an empty ``reference`` is a valid draft row that per-metric
    eligibility (below) excludes only from the context metrics.

Per-metric eligibility
    ``row_is_eligible`` / ``score_metrics_per_eligibility`` score each RAGAS
    metric over its OWN eligible subset (context metrics drop empty-``reference``
    rows) so each aggregate is a mean over real rows — not a skip-NaN mean over a
    hidden partial denominator — and report each metric's scored denominator
    (``n_scored / n_total``). Scores attach back to their originating question by
    per-question key, never positionally, so excluding one row never shifts
    another row's score. The single ragas call is INJECTED as ``score_fn`` so all
    of this stays pure and fully unit-tested; the caller
    (``service_benchmark.get_ragas_results``) supplies the ragas-touching closure.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Legacy authoring dialect -> ragas 0.3.5 modern schema.
LEGACY_TO_MODERN: Dict[str, str] = {
    "question": "user_input",
    "answer": "reference",
    "contexts": "retrieved_contexts",
}

# ragas 0.3.5 metric -> the bank column it additionally REQUIRES beyond
# ``user_input`` / ``response`` / ``retrieved_contexts``. The context metrics
# need the ground-truth ``reference``; the answer metrics do not.
_METRIC_REQUIRED_COLUMN: Dict[str, Optional[str]] = {
    "context_precision": "reference",
    "context_recall": "reference",
    "answer_relevancy": None,
    "faithfulness": None,
}


def normalize_record(record: Any) -> Any:
    """Return ``record`` in the modern ragas dialect.

    Each legacy key is renamed to its modern equivalent UNLESS the modern key is
    already present (a modern-authored record passes through unchanged). Non-dict
    items pass through untouched so the caller's own per-item validation still
    runs. Extension fields are preserved verbatim.
    """
    if not isinstance(record, dict):
        return record
    out = dict(record)
    for legacy, modern in LEGACY_TO_MODERN.items():
        if legacy in out:
            value = out.pop(legacy)
            out.setdefault(modern, value)
    return out


def normalize_bank(records: Any) -> Any:
    """Normalize every record in a bank list; non-list input passes through."""
    if not isinstance(records, list):
        return records
    return [normalize_record(r) for r in records]


def required_fields_for_modes(benchmarking_configs: Any) -> List[str]:
    """Schema-validation field set for the modes being run.

    ``user_input`` is always required; SOURCES mode additionally requires
    ``sources`` (so a modern bank lacking ``sources`` does not silently enter
    SOURCES mode and mis-score). RAGAS mode adds NOTHING — an empty ``reference``
    is valid input, ineligible only for the context metrics. Returns a fresh list
    each call (never accumulates across configs).
    """
    fields = ["user_input"]
    if isinstance(benchmarking_configs, dict) and "SOURCES" in benchmarking_configs:
        fields.append("sources")
    return fields


def metric_required_column(metric: str) -> Optional[str]:
    """The bank column ``metric`` requires be non-empty, or ``None`` if it has no
    extra data requirement beyond the always-present ``user_input``/``response``."""
    return _METRIC_REQUIRED_COLUMN.get(metric)


def row_is_eligible(row: Dict[str, Any], metric: str) -> bool:
    """True if ``row`` populates ``metric``'s required column (if any)."""
    column = metric_required_column(metric)
    if column is None:
        return True
    return bool(row.get(column))


def eligible_subset(
    rows: Sequence[Dict[str, Any]], keys: Sequence[str], metric: str
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Split ``(rows, keys)`` into the parallel subset eligible for ``metric``."""
    elig_rows: List[Dict[str, Any]] = []
    elig_keys: List[str] = []
    for row, key in zip(rows, keys):
        if row_is_eligible(row, metric):
            elig_rows.append(row)
            elig_keys.append(key)
    return elig_rows, elig_keys


def _mean_ignoring_nan(scores: Sequence[float]) -> float:
    """Mean over ``scores``, skipping any NaN cells ragas may emit for a row that
    the judge could not score. NaN here is a per-cell scoring failure inside an
    already-eligible subset, distinct from an ineligible (excluded) row."""
    real = [s for s in scores if isinstance(s, (int, float)) and not math.isnan(s)]
    if not real:
        return math.nan
    return sum(real) / len(real)


def score_metrics_per_eligibility(
    rows: Sequence[Dict[str, Any]],
    keys: Sequence[str],
    metrics: Sequence[str],
    question_wise_results: Dict[str, Dict[str, Any]],
    score_fn: Callable[[str, List[Dict[str, Any]]], Sequence[float]],
) -> Dict[str, Any]:
    """Score each metric over its own eligible subset, attach scores by key, and
    report per-metric aggregates + scored denominators.

    ``rows`` and ``keys`` are parallel: ``keys[i]`` is the ``question_<n>`` key of
    ``rows[i]`` in ``question_wise_results``. For each metric the eligible subset
    is scored via ``score_fn(metric, eligible_rows) -> per-row scores``; the
    per-row score is written onto ``question_wise_results[key][metric]``. A metric
    whose eligible subset is empty records ``n/a`` (a NaN aggregate,
    ``"0 of n_total"``) WITHOUT calling ``score_fn`` — so ragas is never invoked
    on an empty dataset. Returns ``{aggregate_<metric>, <metric>_scored}`` for
    every metric.

    WARNING: mutates ``question_wise_results`` in place.
    """
    total = len(rows)
    out: Dict[str, Any] = {}
    for metric in metrics:
        elig_rows, elig_keys = eligible_subset(rows, keys, metric)
        agg_key = f"aggregate_{metric}"
        scored_key = f"{metric}_scored"
        if not elig_rows:
            out[agg_key] = math.nan
            out[scored_key] = f"0 of {total}"
            continue
        scores = list(score_fn(metric, elig_rows))
        for key, score in zip(elig_keys, scores):
            question_wise_results[key][metric] = score
        out[agg_key] = _mean_ignoring_nan(scores)
        out[scored_key] = f"{len(elig_rows)} of {total}"
    return out
