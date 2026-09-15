"""Unit tests for the end-of-run artifact dump.

``dump_artifacts`` captures ONE timestamp and writes both the JSON artifact and
its markdown report, so the report is always the JSON's ``_report.md`` sibling
— the invariant the backfill script's bulk re-render path depends on. A report
failure must never lose the JSON: the JSON is the source of truth and the
report can be regenerated from it.
"""

from __future__ import annotations

import json
import math
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

    json_files = list(handler_state.glob("*.json"))
    assert len(json_files) == 1
    assert not list(handler_state.glob("*_report.md"))
    # The recovery hint names the exact JSON that was written — the default
    # backfill glob (repo bench_out/) is not where a run's OUTPUT_DIR points.
    assert any(
        "report" in record.getMessage().lower()
        and str(json_files[0]) in record.getMessage()
        for record in caplog.records
    )


# --- #279: the artifact is valid JSON ---------------------------------------


def _strict_loads(text):
    """Parse the way a standards-compliant reader does.

    ``json.loads`` accepts the bare ``NaN``/``Infinity`` tokens Python's own
    writer emits, so the default parser cannot see the bug. ``parse_constant``
    is the hook those tokens go through, so raising there reproduces
    ``JSON.parse`` in a browser and every strict reader.
    """

    def _reject(token):
        raise AssertionError(f"artifact contains the non-JSON token {token!r}")

    return json.loads(text, parse_constant=_reject)


def _unscored_state(monkeypatch):
    """One arm where the judge scored one row and failed the other, so the
    aggregate and one per-question cell are NaN — the exact shape that wrote
    bare ``NaN`` into 10 of the 18 committed ``bench_out/`` artifacts."""
    monkeypatch.setattr(
        ResultHandler,
        "results",
        [
            {
                "configuration_file": "bench.yaml",
                "configuration": {"services": {"benchmarking": {"modes": ["RAGAS"]}}},
                "single_question_results": {
                    "question_1": {
                        "question": "How do I submit a job?",
                        "answer": "Use sbatch.",
                        "reference_answer": "sbatch",
                        "answer_relevancy": 0.0,
                        "context_recall": math.nan,
                    },
                    "question_2": {
                        "question": "How do I request a GPU?",
                        "answer": "Use --gres=gpu:1.",
                        "reference_answer": "--gres=gpu:1",
                        "answer_relevancy": 0.9,
                        "context_recall": 0.0,
                    },
                },
                "total_results": {
                    "aggregate_answer_relevancy": 0.45,
                    "answer_relevancy_scored": "2 of 2",
                    "aggregate_context_recall": math.nan,
                    "context_recall_scored": "0 of 2",
                },
            }
        ],
    )


def test_dump_writes_strict_json_when_a_metric_is_unscored(handler_state, monkeypatch):
    _unscored_state(monkeypatch)

    ResultHandler.dump_artifacts(Path("bench"))

    (json_path,) = list(handler_state.glob("*.json"))
    text = json_path.read_text()
    assert "NaN" not in text and "Infinity" not in text
    document = _strict_loads(text)
    arm = document["benchmarking_results"][0]
    assert arm["total_results"]["aggregate_context_recall"] is None
    assert arm["single_question_results"]["question_1"]["context_recall"] is None


def test_dump_does_not_mutate_the_in_memory_results(handler_state, monkeypatch):
    """The dump writes a copy. ``pair_ab_results`` and ``build_leaderboard``
    both test the live results with ``math.isnan``; rewriting NaN to ``None`` in
    place would make an unscored cell read as an absent metric to them."""
    _unscored_state(monkeypatch)

    ResultHandler.dump_artifacts(Path("bench"))

    arm = ResultHandler.results[0]
    assert math.isnan(arm["total_results"]["aggregate_context_recall"])
    assert math.isnan(arm["single_question_results"]["question_1"]["context_recall"])


def test_unscored_and_zero_stay_distinguishable_on_disk(handler_state, monkeypatch):
    """``null`` means "the judge produced no score"; ``0.0`` means "the judge
    scored it, and the score was zero". Collapsing the two would turn a scoring
    failure into the worst possible grade — the distinction
    ``divergence_from_selected_file`` already preserves by using ``null`` rather
    than ``[]``."""
    _unscored_state(monkeypatch)

    ResultHandler.dump_artifacts(Path("bench"))

    (json_path,) = list(handler_state.glob("*.json"))
    arm = _strict_loads(json_path.read_text())["benchmarking_results"][0]
    rows = arm["single_question_results"]
    assert rows["question_1"]["context_recall"] is None  # unscored
    assert rows["question_2"]["context_recall"] == 0.0  # scored, and it was zero
    assert rows["question_1"]["answer_relevancy"] == 0.0  # scored zero, not null
    # the denominators say the same thing in words
    assert arm["total_results"]["context_recall_scored"] == "0 of 2"
    assert arm["total_results"]["answer_relevancy_scored"] == "2 of 2"
