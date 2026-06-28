"""Dump-level gating test for the prompt-sweep leaderboard.

ResultHandler.dump must include a `leaderboard` key only when the leaderboard
has been populated (a 2+ config sweep), and omit it otherwise (an ordinary
single-config run), leaving today's output otherwise unchanged.
"""

import json
from pathlib import Path

import pytest

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import ResultHandler


@pytest.fixture(autouse=True)
def _reset_and_redirect(tmp_path, monkeypatch):
    """Reset ResultHandler class state and redirect dump output to tmp_path."""
    saved = (
        ResultHandler.results,
        ResultHandler.metadata,
        ResultHandler.leaderboard,
        ResultHandler.ab_comparison,
        ResultHandler.ab_comparisons,
    )
    ResultHandler.results = []
    ResultHandler.metadata = {}
    ResultHandler.leaderboard = {}
    ResultHandler.ab_comparison = {}
    ResultHandler.ab_comparisons = []
    monkeypatch.setattr(sb, "OUTPUT_DIR", tmp_path)
    yield
    (
        ResultHandler.results,
        ResultHandler.metadata,
        ResultHandler.leaderboard,
        ResultHandler.ab_comparison,
        ResultHandler.ab_comparisons,
    ) = saved


def _dump_and_load(tmp_path):
    ResultHandler.dump(Path("test-bench"))
    files = list(tmp_path.glob("test-bench-*.json"))
    assert len(files) == 1, f"expected one dump file, found {files}"
    with open(files[0]) as f:
        return json.load(f)


def test_leaderboard_emitted_for_multi_config(tmp_path):
    ResultHandler.results = [{"total_results": {}}, {"total_results": {}}]
    ResultHandler.leaderboard = {
        "primary_metric": "faithfulness",
        "rows": [],
        "shared_context": {},
    }
    output = _dump_and_load(tmp_path)
    assert "leaderboard" in output
    assert output["leaderboard"]["primary_metric"] == "faithfulness"


def test_no_leaderboard_for_single_config(tmp_path):
    ResultHandler.results = [{"total_results": {}}]
    # leaderboard left empty (single-config run never builds one)
    output = _dump_and_load(tmp_path)
    assert "leaderboard" not in output
    # output otherwise unchanged: the usual top-level keys are present
    assert "benchmarking_results" in output
    assert "metadata" in output
