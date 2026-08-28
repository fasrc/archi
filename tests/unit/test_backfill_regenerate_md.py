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


def test_dict_shaped_results_are_skipped_cleanly(tmp_path):
    """A truthy non-list benchmarking_results must skip, not raise."""
    path = tmp_path / "dictshape.json"
    path.write_text(
        json.dumps({"metadata": {}, "benchmarking_results": {"a": 1}})
    )

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


def test_regenerate_html_still_never_creates(tmp_path):
    """The HTML path keeps its re-render-only contract."""
    json_path = _write_artifact(tmp_path)

    assert backfill.regenerate_html(json_path) is None
    assert not list(tmp_path.glob("*.html"))
