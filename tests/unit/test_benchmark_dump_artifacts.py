"""Unit tests for the end-of-run artifact dump.

``dump_artifacts`` captures ONE timestamp and writes both the JSON artifact and
its markdown report, so the report is always the JSON's ``_report.md`` sibling
— the invariant the backfill script's bulk re-render path depends on. A report
failure must never lose the JSON: the JSON is the source of truth and the
report can be regenerated from it.
"""

from __future__ import annotations

from datetime import datetime as real_datetime
from pathlib import Path

import pytest

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import ResultHandler


@pytest.fixture()
def handler_state(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        ResultHandler,
        "results",
        [
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
    )
    monkeypatch.setattr(ResultHandler, "metadata", {"time": "2026-08-28"})
    monkeypatch.setattr(ResultHandler, "ab_comparison", None)
    monkeypatch.setattr(ResultHandler, "ab_comparisons", [])
    monkeypatch.setattr(ResultHandler, "leaderboard", None)
    return tmp_path


def test_dump_artifacts_writes_json_and_markdown_sibling(handler_state):
    ResultHandler.dump_artifacts(Path("bench"))

    json_files = list(handler_state.glob("*.json"))
    md_files = list(handler_state.glob("*_report.md"))
    assert len(json_files) == 1
    assert len(md_files) == 1
    assert md_files[0].name == f"{json_files[0].stem}_report.md"
    content = md_files[0].read_text()
    assert "# Benchmark Results Comparison" in content
    assert "How do I submit a job?" in content
    # Markdown replaced HTML as the default report.
    assert not list(handler_state.glob("*.html"))


def test_stems_match_across_a_clock_rollover(handler_state, monkeypatch):
    """The timestamp is captured once: even when the clock ticks between the
    two writes, the report stays the JSON's sibling."""

    class _TickingDatetime:
        _tick = 0

        @classmethod
        def now(cls, tz=None):
            cls._tick += 1
            return real_datetime(2026, 8, 28, 12, 0, cls._tick, tzinfo=tz)

    monkeypatch.setattr(sb, "datetime", _TickingDatetime)

    ResultHandler.dump_artifacts(Path("bench"))

    json_files = list(handler_state.glob("*.json"))
    md_files = list(handler_state.glob("*_report.md"))
    assert len(json_files) == 1
    assert len(md_files) == 1
    assert md_files[0].name == f"{json_files[0].stem}_report.md"


def test_report_failure_keeps_the_json(handler_state, monkeypatch, caplog):
    def _boom(*args, **kwargs):
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr(sb, "format_markdown_output", _boom)

    with caplog.at_level("ERROR"):
        ResultHandler.dump_artifacts(Path("bench"))

    assert len(list(handler_state.glob("*.json"))) == 1
    assert not list(handler_state.glob("*_report.md"))
    assert any("report" in record.message.lower() for record in caplog.records)
