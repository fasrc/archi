"""Unit tests for the report CLI's output-format defaults.

Markdown is the default report format; HTML stays available as an opt-in via
``--html_output``. The default markdown file lands next to the input JSON so
the report is always the artifact's ``_report.md`` sibling.
"""

from __future__ import annotations

import json
import sys

import pytest

from src.utils.generate_benchmark_report import main


@pytest.fixture()
def artifact(tmp_path):
    """A minimal valid benchmark artifact."""
    payload = {
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
    path = tmp_path / "bench-20260828_120000.json"
    path.write_text(json.dumps(payload))
    return path


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["generate_benchmark_report.py"] + argv)
    main()


def test_default_writes_markdown_sibling_only(monkeypatch, artifact):
    _run_cli(monkeypatch, [str(artifact)])

    md_path = artifact.with_name("bench-20260828_120000_report.md")
    assert md_path.exists()
    content = md_path.read_text()
    assert "# Benchmark Results Comparison" in content
    assert "How do I submit a job?" in content
    # No HTML file appears anywhere next to the artifact.
    assert not list(artifact.parent.glob("*.html"))


def test_html_flag_alone_writes_only_html(monkeypatch, artifact, tmp_path):
    html_path = tmp_path / "out.html"
    _run_cli(monkeypatch, [str(artifact), "--html_output", str(html_path)])

    assert html_path.exists()
    assert "<html>" in html_path.read_text()
    assert not list(artifact.parent.glob("*.md"))


def test_both_flags_write_both_files(monkeypatch, artifact, tmp_path):
    html_path = tmp_path / "out.html"
    md_path = tmp_path / "out.md"
    _run_cli(
        monkeypatch,
        [
            str(artifact),
            "--html_output",
            str(html_path),
            "--markdown_output",
            str(md_path),
        ],
    )

    assert html_path.exists()
    assert md_path.exists()
    assert "# Benchmark Results Comparison" in md_path.read_text()
