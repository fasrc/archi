"""Resilience helpers for the benchmark run loop.

Isolate per-question failures and degraded (context-overflow) answers so one bad
question never aborts the whole run, and so a degraded answer is never scored as a
clean success or pushed into human evaluation.

The run loop (``src/bin/service_benchmark.py``) is a thin call site over these
pure helpers, which are unit-tested directly.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

# Per-question status values recorded on each ``question_wise_results`` entry.
OK = "ok"
FAILED = "failed"  # answering or scoring raised
DEGRADED = "degraded"  # answered, but the agent marked a context-overflow degradation

# Metadata markers set by the agent's context-overflow handling (base_react:
# ``_handle_context_overflow`` sets ``error_type="context_overflow"`` on the
# hard-failure fallback and ``context_overflow_retry=True`` on a recovered retry).
_DEGRADED_ERROR_TYPES = {"context_overflow"}


def classify_metadata(metadata: Optional[Dict[str, Any]]) -> str:
    """Return ``DEGRADED`` if the agent marked a context-overflow, else ``OK``.

    A degraded answer was produced on truncated context (or is a plain
    context-limit fallback), so it must be distinguishable from a clean,
    full-context success.
    """
    md = metadata or {}
    if md.get("error_type") in _DEGRADED_ERROR_TYPES:
        return DEGRADED
    if md.get("context_overflow_retry"):
        return DEGRADED
    return OK


def build_failure_entry(
    *, question: str, reference_answer: str, error: BaseException
) -> Dict[str, Any]:
    """Build a marked failure entry for a question whose answering/scoring raised."""
    return {
        "question": question,
        "reference_answer": reference_answer,
        "answer": "",
        "status": FAILED,
        "error": f"{type(error).__name__}: {error}",
    }


def is_scorable(q_results: Dict[str, Any]) -> bool:
    """True only for a clean success. Unmarked (legacy) rows count as scorable."""
    return q_results.get("status", OK) == OK


def scorable_items(question_wise_results: Dict[str, Any]) -> Dict[str, Any]:
    """Subset of ``question_wise_results`` that are clean successes."""
    return {k: v for k, v in question_wise_results.items() if is_scorable(v)}


def source_hits(
    matches: Optional[Sequence[bool]], reference_metadata: Sequence[Any]
) -> Tuple[int, int]:
    """Per-question source accuracy contribution as ``(relative_hit, strict_hit)``.

    ``matches`` is ``None`` for a failed/degraded question, contributing nothing.
    A relative hit means any reference source matched; a strict hit means every
    reference source matched.
    """
    if matches is None:
        return (0, 0)
    # A zero-reference row (a `should_refuse` anchor) declares no expected sources,
    # so it can neither hit nor miss one. `all([])` is vacuously true, which booked
    # a FREE strict hit for it regardless of what the SUT actually answered. Such a
    # row is not scorable for source accuracy at all: it contributes to neither
    # numerator, and `Benchmarker._source_scorable_count` keeps it out of the
    # denominator too.
    if not reference_metadata:
        return (0, 0)
    relative = 1 if any(matches) else 0
    strict = 1 if len(matches) == len(reference_metadata) and all(matches) else 0
    return (relative, strict)


# Aggregate output key -> RAGAS metric column.
_RAGAS_AGG = {
    "aggregate_answer_relevancy": "answer_relevancy",
    "aggregate_faithfulness": "faithfulness",
    "aggregate_context_precision": "context_precision",
    "aggregate_context_recall": "context_recall",
}


def build_ragas_aggregates(ragas_results: Any) -> Dict[str, Any]:
    """Mean per RAGAS metric, or ``NaN`` for every metric when there is no scorable
    input (``ragas_results is None`` — an all-failed configuration).

    ``NaN`` (not the string ``"n/a"``) is used so the numeric consumers stay happy:
    the leaderboard already maps ``NaN`` to an incomplete/None metric, and the HTML
    report formats it as ``nan`` without raising on ``float`` / ``:.3f`` (Codex F2).
    """
    if ragas_results is None:
        return {key: float("nan") for key in _RAGAS_AGG}
    return {key: ragas_results[col].mean() for key, col in _RAGAS_AGG.items()}


def build_source_aggregates(
    relative_hits: float, strict_hits: float, total_count: int
) -> Dict[str, Any]:
    """Source-accuracy aggregates over ``total_count`` SOURCE-SCORABLE questions —
    those declaring at least one expected source (see
    ``Benchmarker._source_scorable_count``). Kept numeric so the HTML report's count
    derivation stays consistent. Zero scorable questions -> ``0.0``.

    ``source_scored_count`` travels with the scores because the denominator is no
    longer ``len(questions)``: the report used to re-derive the hit count as
    ``int(len(questions) * accuracy)``, which silently disagrees once zero-source
    rows are excluded. Mirrors the per-metric ``scored_counts`` RAGAS already emits.
    """
    if not total_count:
        return {
            "relative_source_accuracy": 0.0,
            "source_accuracy": 0.0,
            "source_scored_count": 0,
        }
    return {
        "relative_source_accuracy": relative_hits / total_count,
        "source_accuracy": strict_hits / total_count,
        "source_scored_count": total_count,
    }
