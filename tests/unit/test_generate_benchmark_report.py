"""Unit tests for the benchmark HTML report generator.

Regression coverage for the ``benchmark-run-resilience`` capability: the report
generator must tolerate degraded/failed rows. The harness only stamps ``matched``
onto a reference source when the row is a clean success, so a degraded
(context-overflow) row carries ``reference_sources_metadata`` entries *without* a
``matched`` key. Before the fix, such a row raised ``KeyError('matched')`` and
aborted HTML rendering *after* the scores had already been computed and dumped.
"""

from __future__ import annotations

from src.utils.generate_benchmark_report import format_html_output

# Config with both scoring modes on, so the report renders the SOURCES retrieval
# tally and the RAGAS metric blocks (the code paths that read per-row fields).
_CONFIG = {"services": {"benchmarking": {"modes": ["RAGAS", "SOURCES"]}}}

# Aggregates mirror a real floor run (source_accuracy == 0.0 exercises the
# falsy-accuracy branch of the retrieval header).
_TOTALS = {
    "aggregate_answer_relevancy": 0.862,
    "aggregate_faithfulness": 0.594,
    "aggregate_context_precision": 0.501,
    "aggregate_context_recall": 0.667,
    "relative_source_accuracy": 0.556,
    "source_accuracy": 0.0,
}


def _ok_row():
    """A clean, scored row: its reference source carries ``matched``."""
    return {
        "question": "How do I submit a batch job on the cluster?",
        "status": "ok",
        "answer": "Use sbatch with a submission script.",
        "reference_answer": "Submit with sbatch.",
        "reference_sources_metadata": [
            {"url": "https://docs.example/slurm", "matched": True}
        ],
        "reference_sources_match_fields": ["url"],
        "sources_metadata": [
            {"url": "https://docs.example/slurm", "display_name": "slurm"}
        ],
        "answer_relevancy": 0.99,
        "faithfulness": 0.80,
        "context_precision": 0.70,
        "context_recall": 0.60,
    }


def _degraded_row():
    """A degraded (context-overflow) row: reference metadata is present, but the
    harness never stamped ``matched`` because the answer was not source-scored."""
    return {
        "question": "How do I request an interactive GPU session for 2 hours?",
        "status": "degraded",
        "answer": "",
        "reference_answer": "salloc -p gpu --gres=gpu:1 -t 2:00:00",
        "reference_sources_metadata": [{"url": "https://docs.example/gpu"}],
        "reference_sources_match_fields": ["url"],
    }


def test_report_renders_with_degraded_row_missing_matched():
    """A degraded row lacking ``matched`` must not abort report rendering."""
    questions = {"question_1": _ok_row(), "question_2": _degraded_row()}

    html = format_html_output(_CONFIG, "ragas-bench", "2026-07-04", questions, _TOTALS)

    assert isinstance(html, str)
    # Both question cards rendered — the degraded row did not crash the report.
    assert "Question 1: question_1" in html
    assert "Question 2: question_2" in html
    # The degraded row's expected source is shown; with no `matched` it counts as
    # a miss (INCORRECT), never as source-correct.
    assert "https://docs.example/gpu" in html
    assert "INCORRECT" in html


def test_report_renders_per_question_answer_correctness_score():
    """The PER-QUESTION RAGAS block renders from a fixed key -> display-name map,
    so a metric absent from that map is computed and dumped but never shown on
    the question card.

    The run-summary section is deliberately excluded from this test: it derives
    its labels generically from the ``aggregate_*`` keys, so leaving the
    aggregate out of ``totals`` makes the per-question map the ONLY thing that
    can produce the label.
    """
    row = _ok_row()
    row["answer_correctness"] = 0.42

    html = format_html_output(
        _CONFIG, "ragas-bench", "2026-08-20", {"question_1": row}, _TOTALS
    )

    assert "aggregate_answer_correctness" not in str(_TOTALS)
    assert "Answer Correctness" in html
    assert "0.420" in html


# --- #279: unscored cells in the HTML report --------------------------------


def test_html_renders_null_aggregate_as_unscored():
    """From #279 on, an unscored metric is serialized as ``null``, so every
    reader sees ``None`` where it used to see NaN. The HTML aggregate loop
    compared the value against 0.5 with no guard, which raises ``TypeError:
    '<' not supported between instances of 'NoneType' and 'float'`` and takes
    the whole report down."""
    totals = dict(_TOTALS, aggregate_context_recall=None)

    html = format_html_output(
        _CONFIG, "ragas-bench", "2026-09-03", {"question_1": _ok_row()}, totals
    )

    assert "Context Recall" in html
    assert "n/a (unscored)" in html


def test_html_renders_nan_aggregate_as_unscored_not_nan():
    """The older artifacts still on disk carry NaN, and NaN fails both threshold
    comparisons — so without a guard it wore the green ``score-high`` badge and
    printed the literal ``nan`` as its value, reading as a success."""
    totals = dict(_TOTALS, aggregate_context_recall=float("nan"))

    html = format_html_output(
        _CONFIG, "ragas-bench", "2026-09-03", {"question_1": _ok_row()}, totals
    )

    assert ">nan<" not in html
    assert "n/a (unscored)" in html
    # the unscored metric must not be badged as a high score
    assert "nan</div>" not in html


def test_html_renders_null_and_nan_question_cells_as_unscored():
    """Same defect one level down: the per-question block skipped ``None`` but
    formatted NaN with ``:.3f``, printing ``nan`` on the question card."""
    row = _ok_row()
    row["context_recall"] = float("nan")
    row["context_precision"] = None

    html = format_html_output(
        _CONFIG, "ragas-bench", "2026-09-03", {"question_1": row}, _TOTALS
    )

    assert ">nan<" not in html
    assert "Context Recall" in html
    assert "Context Precision" in html
    assert html.count("n/a (unscored)") >= 2


def test_html_keeps_a_scored_zero_visible_as_a_score():
    """The complement: 0.0 is a score, not an absence. It keeps its numeric cell
    and its red badge instead of being folded into "unscored"."""
    row = _ok_row()
    row["context_recall"] = 0.0
    totals = dict(_TOTALS, aggregate_context_recall=0.0)

    html = format_html_output(
        _CONFIG, "ragas-bench", "2026-09-03", {"question_1": row}, totals
    )

    assert "0.000" in html
    assert "n/a (unscored)" not in html
