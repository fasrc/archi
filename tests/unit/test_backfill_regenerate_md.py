"""Unit tests for the backfill script's markdown render-and-recover path.

``--regenerate-md`` differs from ``--regenerate-html`` on purpose: markdown is
the run's default report, so a valid artifact without its ``_report.md``
sibling is a recoverable gap (a report write that failed after the JSON
landed), and the script creates it. A file that is not a parseable benchmark
artifact is skipped cleanly — no file, no error.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "benchmarking"
    / "backfill_report_provenance.py"
)
_spec = importlib.util.spec_from_file_location("backfill_report_provenance", _SCRIPT)
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)


def _artifact_payload():
    return {
        "benchmarking_results": [
            {
                "configuration_file": "bench.yaml",
                "configuration": {"services": {"benchmarking": {"modes": ["RAGAS"]}}},
                "single_question_results": {
                    "q1": {
                        "question": "How do I submit a job?",
                        "answer": "Use sbatch.",
                        "reference_answer": "sbatch",
                        "answer_relevancy": 0.9,
                    }
                },
                "total_results": {"aggregate_answer_relevancy": 0.9},
            }
        ],
        "metadata": {"time": "2026-08-28"},
    }


def _write_artifact(tmp_path):
    path = tmp_path / "bench-20260828_120000.json"
    path.write_text(json.dumps(_artifact_payload()))
    return path


def test_rerenders_an_existing_sibling(tmp_path):
    json_path = _write_artifact(tmp_path)
    md_path = tmp_path / "bench-20260828_120000_report.md"
    md_path.write_text("stale report")

    note = backfill.regenerate_md(json_path)

    assert note == f"re-rendered {md_path.name}"
    content = md_path.read_text()
    assert "stale report" not in content
    assert "# Benchmark Results Comparison" in content


def test_creates_a_missing_sibling(tmp_path):
    json_path = _write_artifact(tmp_path)
    md_path = tmp_path / "bench-20260828_120000_report.md"
    assert not md_path.exists()

    note = backfill.regenerate_md(json_path)

    assert note == f"created {md_path.name}"
    assert "How do I submit a job?" in md_path.read_text()


def test_dry_run_writes_nothing(tmp_path):
    json_path = _write_artifact(tmp_path)

    note = backfill.regenerate_md(json_path, dry_run=True)

    assert note.startswith("would create")
    assert not list(tmp_path.glob("*.md"))


def test_foreign_metadata_json_is_skipped_cleanly(tmp_path):
    """A metadata-bearing JSON without parseable benchmarking_results passes
    the script's NOT_AN_ARTIFACT check but must not gain a bogus report."""
    path = tmp_path / "foreign.json"
    path.write_text(json.dumps({"metadata": {"time": "x"}, "other": 1}))

    assert backfill.regenerate_md(path) is None
    assert not list(tmp_path.glob("*.md"))


def test_shapeless_result_record_is_skipped_cleanly(tmp_path):
    """parse_benchmark_results defaults every field, so an empty record would
    otherwise gain a plausible-looking report; the validator must require the
    fields the renderer actually consumes."""
    path = tmp_path / "shapeless.json"
    path.write_text(
        json.dumps({"metadata": {"time": "x"}, "benchmarking_results": [{}]})
    )

    assert backfill.regenerate_md(path) is None
    assert not list(tmp_path.glob("*.md"))


def test_render_failure_after_validation_skips_cleanly(tmp_path):
    """A record can pass the key checks and still blow up the renderer (for
    example `configuration: []`); the failure must skip, not abort the bulk
    run with an exception main() does not catch."""
    payload = _artifact_payload()
    payload["benchmarking_results"][0]["configuration"] = []
    path = tmp_path / "listconfig.json"
    path.write_text(json.dumps(payload))

    assert backfill.regenerate_md(path) is None
    assert not list(tmp_path.glob("*.md"))


def test_dict_shaped_results_are_skipped_cleanly(tmp_path):
    """A truthy non-list benchmarking_results must skip, not raise."""
    path = tmp_path / "dictshape.json"
    path.write_text(json.dumps({"metadata": {}, "benchmarking_results": {"a": 1}}))

    assert backfill.regenerate_md(path) is None
    assert not list(tmp_path.glob("*.md"))


def test_non_dict_result_record_is_skipped_cleanly(tmp_path):
    path = tmp_path / "weird.json"
    path.write_text(
        json.dumps({"metadata": {}, "benchmarking_results": ["not a record"]})
    )

    assert backfill.regenerate_md(path) is None
    assert not list(tmp_path.glob("*.md"))


def test_empty_results_list_is_skipped_cleanly(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"metadata": {}, "benchmarking_results": []}))

    assert backfill.regenerate_md(path) is None
    assert not list(tmp_path.glob("*.md"))


def test_non_dict_json_is_skipped_cleanly(tmp_path):
    path = tmp_path / "list.json"
    path.write_text(json.dumps([1, 2, 3]))

    assert backfill.regenerate_md(path) is None
    assert not list(tmp_path.glob("*.md"))


def test_stamp_file_does_not_rewrite_foreign_metadata_json(tmp_path):
    """The stamping pass runs before regeneration in the CLI flow: a foreign
    dict with a metadata key must be classified NOT_AN_ARTIFACT, not gain
    provenance fields."""
    path = tmp_path / "foreign.json"
    original = {"metadata": {}, "other": 1}
    path.write_text(json.dumps(original))

    status = backfill.stamp_file(path)

    assert status == backfill.NOT_AN_ARTIFACT
    assert json.loads(path.read_text()) == original


def test_stamp_file_skips_null_metadata_without_raising(tmp_path):
    """`metadata: null` used to raise an uncaught TypeError on the membership
    check and abort the whole bulk run."""
    path = tmp_path / "nullmeta.json"
    path.write_text(json.dumps({"metadata": None, "benchmarking_results": []}))

    assert backfill.stamp_file(path) == backfill.NOT_AN_ARTIFACT


def test_regenerate_html_still_never_creates(tmp_path):
    """The HTML path keeps its re-render-only contract."""
    json_path = _write_artifact(tmp_path)

    assert backfill.regenerate_html(json_path) is None
    assert not list(tmp_path.glob("*.html"))


def test_rerenders_html_for_an_artifact_with_null_scores(tmp_path):
    """#279 end to end: from now on an unscored metric is ``null`` on disk, and
    the backfill script is the tool that re-renders old reports. The HTML
    renderer compared the aggregate against 0.5 with no guard, so the first
    ``null`` artifact fed through ``--regenerate-html`` raised ``TypeError`` and
    aborted the bulk run part-way."""
    payload = _artifact_payload()
    arm = payload["benchmarking_results"][0]
    arm["single_question_results"]["q1"]["context_recall"] = None
    arm["total_results"]["aggregate_context_recall"] = None
    json_path = tmp_path / "bench-20260828_120000.json"
    json_path.write_text(json.dumps(payload))
    html_path = tmp_path / "bench-20260828_120000_report.html"
    html_path.write_text("stale report")

    note = backfill.regenerate_html(json_path)

    assert note == f"re-rendered {html_path.name}"
    content = html_path.read_text()
    assert "stale report" not in content
    assert "n/a (unscored)" in content


def test_rerenders_md_for_an_artifact_with_null_scores(tmp_path):
    """The markdown path already routes scores through ``_score_cell``; this
    pins that a ``null`` cell reads the same way there, so the two reports do
    not disagree about what an unscored metric looks like."""
    payload = _artifact_payload()
    arm = payload["benchmarking_results"][0]
    arm["single_question_results"]["q1"]["answer_relevancy"] = None
    arm["total_results"]["aggregate_answer_relevancy"] = None
    json_path = tmp_path / "bench-20260828_120000.json"
    json_path.write_text(json.dumps(payload))

    note = backfill.regenerate_md(json_path)

    assert note == "created bench-20260828_120000_report.md"
    assert (
        "n/a (unscored)" in (tmp_path / "bench-20260828_120000_report.md").read_text()
    )
