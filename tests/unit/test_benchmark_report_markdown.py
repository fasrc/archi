"""Unit tests for the benchmark markdown report renderer.

Coverage for the ``benchmark-report-rendering`` capability: the markdown report
carries the same content as the HTML report, neutralizes artifact-sourced text
(fences for long-form fields, escapes for inline fields), and stays renderable
for artifacts that predate provenance stamping.
"""

from __future__ import annotations

from src.utils.generate_benchmark_report import (
    _format_seconds,
    format_html_output,
    format_markdown_output,
)

_CONFIG = {"services": {"benchmarking": {"modes": ["RAGAS", "SOURCES"]}}}

_TOTALS = {
    "aggregate_answer_relevancy": 0.862,
    "aggregate_faithfulness": 0.594,
    "aggregate_context_precision": 0.501,
    "aggregate_context_recall": 0.667,
    "relative_source_accuracy": 0.556,
    "source_accuracy": 0.0,
}

_PROVENANCE = {
    "running_configuration": None,
    "configuration_divergence": [],
    "corpus_fingerprint_before": "abc123",
    "corpus_fingerprint": "abc123",
    "corpus_unchanged_at_endpoints": True,
    "config_version": {"digest": "cfg-digest-1", "source": "postgres"},
    "code_version": {"digest": "code-digest-1"},
}


def _ok_row():
    return {
        "question": "How do I submit a batch job on the cluster?",
        "status": "ok",
        "answer": "Use sbatch with a submission script, e.g. sbatch job.sh.",
        "reference_answer": "Submit with sbatch.",
        "reference_sources_metadata": [
            {"url": "https://docs.example/slurm", "matched": True}
        ],
        "reference_sources_match_fields": ["url"],
        "sources_metadata": [
            {"url": "https://docs.example/slurm", "display_name": "slurm"}
        ],
        "contexts": ["Slurm batch jobs are submitted with sbatch."],
        "answer_relevancy": 0.99,
        "faithfulness": 0.80,
        "context_precision": 0.70,
        "context_recall": 0.60,
    }


def test_markdown_header_renders_run_identity():
    md = format_markdown_output(
        _CONFIG,
        "ragas-bench",
        "2026-08-28",
        {"question_1": _ok_row()},
        _TOTALS,
        _PROVENANCE,
    )

    assert isinstance(md, str)
    assert "# Benchmark Results Comparison" in md
    assert "**Configuration:** ragas-bench" in md
    assert "**Timestamp:** 2026-08-28" in md
    assert "**Questions Processed:** 1" in md
    # No HTML skeleton leaks into the markdown report.
    assert "<!DOCTYPE" not in md
    assert "<body>" not in md


def test_markdown_renders_aggregate_ragas_metrics():
    md = format_markdown_output(
        _CONFIG,
        "ragas-bench",
        "2026-08-28",
        {"question_1": _ok_row()},
        _TOTALS,
        _PROVENANCE,
    )

    assert "Aggregate RAGAS Metrics" in md
    assert "Answer Relevancy" in md
    assert "0.862" in md
    # Threshold badges: >= 0.7 green, [0.5, 0.7) yellow, < 0.5 red.
    assert "0.862 🟢" in md
    assert "0.594 🟡" in md
    # aggregate_context_precision is 0.501 -> yellow, nothing at red here;
    # the badge helper's red edge is covered in the threshold test below.


def test_markdown_renders_per_question_content():
    md = format_markdown_output(
        _CONFIG,
        "ragas-bench",
        "2026-08-28",
        {"question_1": _ok_row()},
        _TOTALS,
        _PROVENANCE,
    )

    # The qid is inline-escaped, so its underscore carries a backslash.
    assert "Question 1: question\\_1" in md
    assert "How do I submit a batch job on the cluster?" in md
    assert "Use sbatch with a submission script" in md
    assert "Submit with sbatch." in md
    # Per-question RAGAS scores render with their display names.
    assert "Answer Relevancy" in md
    assert "0.990" in md
    # The retrieval check shows expected and retrieved documents; the URL's
    # colon carries the autolink-defusing backslash.
    assert "https\\://docs.example/slurm" in md
    assert "FULLY CORRECT" in md


def test_fenced_content_renders_literally():
    """Placeholders and backtick runs must never terminate their fence."""
    row = _ok_row()
    row["answer"] = "Run sbatch <jobid> then check:\n```\nsqueue -u <rcusername>\n```"

    md = format_markdown_output(
        _CONFIG, "ragas-bench", "2026-08-28", {"q": row}, _TOTALS, _PROVENANCE
    )

    # The literal placeholder survives inside the fence.
    assert "<jobid>" in md
    assert "<rcusername>" in md
    # The fence around the answer is longer than the 3-backtick run inside it.
    assert "````text\nRun sbatch <jobid>" in md


def test_badge_thresholds():
    totals = {
        "aggregate_low": 0.49,
        "aggregate_mid": 0.5,
        "aggregate_high": 0.7,
    }

    md = format_markdown_output(
        {"services": {"benchmarking": {"modes": ["RAGAS"]}}},
        "bench",
        "2026-08-28",
        {},
        totals,
        None,
    )

    assert "0.490 🔴" in md
    assert "0.500 🟡" in md
    assert "0.700 🟢" in md


def test_renders_without_provenance():
    """Artifacts that predate provenance stamping still render."""
    md = format_markdown_output(
        _CONFIG, "ragas-bench", "2026-08-28", {"q": _ok_row()}, _TOTALS, None
    )

    assert "Run provenance" not in md
    assert "Question 1: q" in md


def test_missing_provenance_keys_report_not_recorded():
    """Absence is not agreement: missing keys say so instead of guessing."""
    md = format_markdown_output(
        _CONFIG,
        "ragas-bench",
        "2026-08-28",
        {"q": _ok_row()},
        _TOTALS,
        {"configuration_divergence": None, "code_version": {"deploy_git_commit": "x"}},
    )

    assert "not recorded" in md
    assert "unknown" in md


def test_sources_mode_retrieval_accuracy_section():
    totals = {
        "source_accuracy": 0.5,
        "source_scored_count": 4,
        "relative_source_accuracy": 0.75,
    }

    md = format_markdown_output(
        {"services": {"benchmarking": {"modes": ["SOURCES"]}}},
        "bench",
        "2026-08-28",
        {},
        totals,
        None,
    )

    assert "Retrieval Accuracy" in md
    assert "**Fully Correct:** 2/4 (50.0%)" in md
    assert "**Partially Correct** (some expected sources retrieved): 1" in md
    assert "**Incorrect** (no expected sources retrieved): 1" in md


def test_inline_fields_cannot_restructure_the_report():
    """Markdown or raw HTML in artifact data renders as text, not structure."""
    row = _ok_row()
    row["question"] = "# fake heading\n[link](https://evil.example) <img src=x>"
    row["reference_sources_metadata"] = [{"url": "evil|cell *bold*", "matched": True}]
    row["sources_metadata"] = [{"display_name": "evil|cell *bold*"}]

    md = format_markdown_output(
        _CONFIG,
        "*bold* <script>alert(1)</script>",
        "2026-08-28",
        {"q": row},
        _TOTALS,
        None,
    )

    # Raw HTML is neutralized everywhere outside fences.
    assert "<img src=x>" not in md
    assert "<script>" not in md
    # Link syntax is defused.
    assert "[link](https://evil.example)" not in md
    # The newline collapse keeps data off line starts, so no injected heading.
    assert "\n# fake heading" not in md
    # Emphasis and table pipes are escaped in the config name and source names.
    assert "\\*bold\\*" in md
    assert "evil\\|cell" in md


def test_nan_scores_render_unscored_not_green():
    """build_ragas_aggregates emits NaN when nothing was scorable; NaN fails
    both threshold comparisons, so it must not wear the green badge."""
    row = _ok_row()
    row["answer_relevancy"] = float("nan")

    md = format_markdown_output(
        {"services": {"benchmarking": {"modes": ["RAGAS"]}}},
        "bench",
        "2026-08-28",
        {"q": row},
        {"aggregate_faithfulness": float("nan")},
        None,
    )

    assert "nan 🟢" not in md
    assert "n/a (unscored)" in md


def test_markdown_omits_the_retrieval_section_when_no_source_metrics_were_recorded():
    """The markdown mirror of the HTML retrieval guard (#279, "also noticed"):
    ``SOURCES`` in the modes with no source metrics recorded reached
    ``round(ret_total * None)`` and took the default report down with it."""
    totals = {"aggregate_answer_relevancy": 0.862}

    md = format_markdown_output(
        _CONFIG, "bench", "2026-09-03", {"q": _ok_row()}, totals, None
    )

    assert "Retrieval Accuracy" not in md
    assert "Aggregate RAGAS Metrics" in md


def test_markdown_omits_the_retrieval_section_when_nothing_was_source_scorable():
    """Mirror of the HTML case: ``source_scored_count: 0`` is an empty sample,
    and "0/0 (0.0%)" reads as a measured retrieval collapse instead."""
    totals = {
        "source_accuracy": 0.0,
        "relative_source_accuracy": 0.0,
        "source_scored_count": 0,
    }

    md = format_markdown_output(
        _CONFIG, "bench", "2026-09-03", {"q": _ok_row()}, totals, None
    )

    assert "Retrieval Accuracy" not in md


def test_the_two_reports_agree_on_the_retrieval_tally():
    """The HTML report reconstructed its counts with ``int()`` and the markdown
    report with ``round()``, so one artifact yielded two different tallies. They
    are rendered from the same numbers and must say the same thing."""
    totals = {
        "source_accuracy": 15 / 22,
        "relative_source_accuracy": 17 / 22,
        "source_scored_count": 22,
    }
    questions = {"q": _ok_row()}

    md = format_markdown_output(_CONFIG, "bench", "2026-09-03", questions, totals, None)
    html = format_html_output(_CONFIG, "bench", "2026-09-03", questions, totals)

    assert "- **Fully Correct:** 15/22 (68.2%)" in md
    assert "Fully Correct: 15/22" in html
    assert "- **Partially Correct** (some expected sources retrieved): 2" in md
    assert "- **Incorrect** (no expected sources retrieved): 5" in md


def test_markdown_still_renders_the_retrieval_section_when_the_metrics_are_there():
    """0.0 accuracy is a measured floor result; the guard must not hide it."""
    md = format_markdown_output(
        _CONFIG, "bench", "2026-09-03", {"q": _ok_row()}, _TOTALS, None
    )

    assert "Retrieval Accuracy" in md


def test_null_question_score_renders_unscored_rather_than_vanishing():
    """#279 spells an unscored cell ``null`` on disk. The question card used to
    drop a ``None`` row entirely, which reads identically to a metric the config
    never enabled — the exact confusion the scored denominator exists to stop."""
    row = _ok_row()
    row["context_recall"] = None

    md = format_markdown_output(
        _CONFIG, "bench", "2026-09-03", {"q": row}, _TOTALS, None
    )

    assert "| Context Recall | n/a (unscored) |" in md
    # a metric the run never scored still has no row at all
    assert "Answer Correctness" not in md


def test_long_context_keeps_the_full_text_available():
    """The HTML report exposes the full document behind an expander; the
    markdown report must not permanently drop the evidence past the preview."""
    row = _ok_row()
    row["contexts"] = ["A" * 500 + "TAIL-BEYOND-THE-PREVIEW"]

    md = format_markdown_output(
        _CONFIG, "bench", "2026-08-28", {"q": row}, _TOTALS, None
    )

    assert "Show full document" in md
    assert "TAIL-BEYOND-THE-PREVIEW" in md


def test_emphasis_strikethrough_and_autolinks_are_neutralized():
    row = _ok_row()
    row["question"] = (
        "_italic_ ~~strike~~ visit https://evil.example or www.evil.example"
    )

    md = format_markdown_output(
        _CONFIG, "bench", "2026-08-28", {"q": row}, _TOTALS, None
    )

    assert "\\_italic\\_" in md
    assert "\\~\\~strike\\~\\~" in md
    # An escaped colon or dot cannot participate in a GFM autolink.
    assert "https\\://evil.example" in md
    assert "www\\.evil.example" in md


def test_email_and_parenthesized_www_autolinks_are_neutralized():
    """GFM autolinks emails and www. after an opening parenthesis too."""
    row = _ok_row()
    row["question"] = "Mail support@example.com (www.evil.example) please"

    md = format_markdown_output(
        _CONFIG, "bench", "2026-08-28", {"q": row}, _TOTALS, None
    )

    assert "support\\@example.com" in md
    assert "(www\\.evil.example)" in md


def test_source_hit_counts_survive_float_reconstruction():
    """15/22 stored as a float must reconstruct to 15 hits, not truncate to
    14 — int() drops the .999… that binary floating point leaves behind."""
    totals = {
        "source_accuracy": 15 / 22,
        "source_scored_count": 22,
        "relative_source_accuracy": 15 / 22,
    }

    md = format_markdown_output(
        {"services": {"benchmarking": {"modes": ["SOURCES"]}}},
        "bench",
        "2026-08-28",
        {},
        totals,
        None,
    )

    assert "15/22 (68.2%)" in md
    assert "(no expected sources retrieved): 7" in md


def test_leading_ordered_list_marker_is_defused():
    """A field that begins like `1. item` must not become a list item."""
    row = _ok_row()
    row["question"] = "1. injected list item"

    md = format_markdown_output(
        _CONFIG, "bench", "2026-08-28", {"q": row}, _TOTALS, None
    )

    assert "\n1\\. injected list item" in md
    assert "\n1. injected list item" not in md


def test_aggregate_metric_labels_are_escaped():
    """A crafted aggregate key must not split the table row or inject markup."""
    totals = {"aggregate_x|y`z": 0.9}

    md = format_markdown_output(
        {"services": {"benchmarking": {"modes": ["RAGAS"]}}},
        "bench",
        "2026-08-28",
        {},
        totals,
        None,
    )

    assert "X\\|Y\\`Z" in md
    assert "| X|Y" not in md


def test_data_cannot_close_a_code_span():
    """Digests and fingerprints render in code spans; a backtick inside the
    data must not terminate the span early."""
    provenance = {
        "configuration_divergence": ["evil` [link](x)"],
        "corpus_unchanged_at_endpoints": True,
        "corpus_fingerprint": "abc`def",
        "corpus_fingerprint_before": "abc`def",
        "code_version": {"digest": "dig`it"},
        "config_version": {"digest": "cfg`digest"},
    }

    md = format_markdown_output(
        _CONFIG, "bench", "2026-08-28", {"q": _ok_row()}, _TOTALS, provenance
    )

    # Padded code spans: the data's backtick run is shorter than the delimiter.
    assert "`` abc`def ``" in md
    assert "`` dig`it ``" in md
    assert "`` cfg`digest ``" in md
    # Inside a padded code span the content renders literally, so the raw
    # divergence item appears unescaped between the delimiters.
    assert "`` evil` [link](x) ``" in md


def test_markdown_renders_provenance_section():
    md = format_markdown_output(
        _CONFIG,
        "ragas-bench",
        "2026-08-28",
        {"question_1": _ok_row()},
        _TOTALS,
        _PROVENANCE,
    )

    assert "Run provenance" in md
    # An empty divergence list means "compared and agreed".
    assert "matches" in md
    assert "cfg-digest-1" in md
    assert "code-digest-1" in md


def _provenance_md(**overrides):
    provenance = dict(_PROVENANCE)
    provenance.update(overrides)
    return format_markdown_output(
        _CONFIG,
        "ragas-bench",
        "2026-08-28",
        {"question_1": _ok_row()},
        _TOTALS,
        provenance,
    )


def test_provenance_shows_time_to_ingest():
    """Seconds for arithmetic, h/m/s so a person can read it (#417)."""
    md = _provenance_md(ingest_wall_seconds=7351.2)

    assert "Time to ingest" in md
    assert "7351 s" in md
    assert "2h 2m 31s" in md
    # The wait happens once, before the sweep, so a reader must not attribute
    # the number to this arm's corpus in particular. The check that catches
    # that is comparing `corpus_fingerprint` across arms -- NOT
    # `corpus_unchanged_at_endpoints`, which reads True on both sides of a
    # re-ingest that lands wholly between two arms.
    assert "every arm" in md
    assert "corpus_fingerprint" in md
    # Nor is it a clean bound in either direction: work before the first poll
    # is missing, non-ingest time after it is included.
    assert "approximation" in md


def test_provenance_says_not_measured_for_a_reused_corpus():
    """`null` = the run found the corpus already ingested. Not "0 seconds"."""
    md = _provenance_md(ingest_wall_seconds=None)

    assert "reused an existing corpus" in md
    assert "not measured" in md
    assert "0 s" not in md


def test_provenance_says_not_recorded_for_an_older_artifact():
    """Key absent = the artifact predates the field, which is a third thing."""
    md = _provenance_md()

    assert "ingest_wall_seconds" not in _PROVENANCE
    assert "Time to ingest" in md
    assert "predates the field" in md
    assert "reused an existing corpus" not in md


def test_format_seconds_reads_at_every_scale():
    """Seconds always; the h/m/s gloss only where it adds something.

    A sub-minute duration needs no gloss at all, and one under an hour must not
    pad itself out to "0h" -- the gloss exists to be read, not to be uniform.
    """
    assert _format_seconds(42.4) == "42 s"
    assert _format_seconds(1832) == "1832 s (30m 32s)"
    assert _format_seconds(7351.2) == "7351 s (2h 2m 31s)"
