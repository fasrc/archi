"""Unit tests for the benchmark markdown report renderer.

Coverage for the ``benchmark-report-rendering`` capability: the markdown report
carries the same content as the HTML report, neutralizes artifact-sourced text
(fences for long-form fields, escapes for inline fields), and stays renderable
for artifacts that predate provenance stamping.
"""

from __future__ import annotations

from src.utils.generate_benchmark_report import format_markdown_output

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

    assert "Question 1: question_1" in md
    assert "How do I submit a batch job on the cluster?" in md
    assert "Use sbatch with a submission script" in md
    assert "Submit with sbatch." in md
    # Per-question RAGAS scores render with their display names.
    assert "Answer Relevancy" in md
    assert "0.990" in md
    # The retrieval check shows expected and retrieved documents.
    assert "https://docs.example/slurm" in md
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
