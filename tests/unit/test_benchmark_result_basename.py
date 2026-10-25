"""Tests for the fixed result basename picked before the first arm (design D4).

The result basename (<benchmark_name>-<timestamp>) must be fixed once, before the
first arm, so that bundle writers can name themselves after the result JSON. Both
dump_artifacts and the bundle writer receive the same timestamp from run().
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import Benchmarker, ResultHandler

_ARM_ENTRY = {
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
    "category_map_sha256_end": "sha256:x",
}


@pytest.fixture()
def two_arm_state(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(ResultHandler, "_corpus_snapshot_id", None)
    monkeypatch.setattr(
        ResultHandler,
        "results",
        [dict(_ARM_ENTRY), dict(_ARM_ENTRY)],
    )
    monkeypatch.setattr(
        ResultHandler,
        "category_map_records_by_arm",
        [["a\tA"], ["b\tB"]],
    )
    monkeypatch.setattr(ResultHandler, "metadata", {"time": "2026-01-01"})
    monkeypatch.setattr(ResultHandler, "ab_comparison", None)
    monkeypatch.setattr(ResultHandler, "ab_comparisons", [])
    monkeypatch.setattr(ResultHandler, "leaderboard", None)
    return tmp_path


def test_dump_artifacts_fixed_timestamp_writes_correct_names(two_arm_state):
    ResultHandler.dump_artifacts(Path("bench"), timestamp="20260101_000000")

    names = {p.name for p in two_arm_state.iterdir()}
    expected = {
        "bench-20260101_000000.json",
        "bench-20260101_000000_report.md",
        "bench-20260101_000000_category_map_1.tsv",
        "bench-20260101_000000_category_map_2.tsv",
    }
    assert names == expected

    tsv_names = sorted(p.name for p in two_arm_state.glob("*_category_map_*.tsv"))
    assert tsv_names == [
        "bench-20260101_000000_category_map_1.tsv",
        "bench-20260101_000000_category_map_2.tsv",
    ]


def test_dump_artifacts_no_timestamp_still_works(two_arm_state):
    ResultHandler.dump_artifacts(Path("bench"))

    json_files = list(two_arm_state.glob("*.json"))
    md_files = list(two_arm_state.glob("*_report.md"))
    tsv_files = list(two_arm_state.glob("*_category_map_*.tsv"))
    assert len(json_files) == 1
    assert len(md_files) == 1
    assert len(tsv_files) == 2


def test_run_records_timestamp_before_first_process_config(monkeypatch):
    calls = []
    dump_kwargs = []

    def _fake_timestamp():
        calls.append("result_timestamp")
        return "20260101_000000"

    class _RunStub(Benchmarker):
        def __init__(self):
            self.benchmarking_configs = {"modes": ["RAGAS"]}
            self.benchmark_name = "bench"
            self.queries_to_answers = []
            self.all_config_files = ["a.yaml", "b.yaml"]
            self.current_config = "a.yaml"
            self.chain = None
            self.config = {}

        def wait_for_ingestion_completion(self):
            return 0.0

        def _merge_anchor_questions(self):
            return None

        def _process_config(self, modes_being_run):
            calls.append("process_config")
            return {}, {}

        def load_new_configuration(self):
            self.all_config_files.pop(0)
            if self.all_config_files:
                self.current_config = self.all_config_files[0]

    monkeypatch.setattr(ResultHandler, "result_timestamp", _fake_timestamp)
    monkeypatch.setattr(ResultHandler, "check_collection", lambda cfg: None)
    monkeypatch.setattr(ResultHandler, "get_corpus_fingerprint", lambda cfg: None)
    monkeypatch.setattr(ResultHandler, "get_category_map", lambda cfg: (None, None))
    monkeypatch.setattr(ResultHandler, "handle_results", lambda *a, **kw: None)
    monkeypatch.setattr(ResultHandler, "add_metadata", lambda: None)
    monkeypatch.setattr(
        ResultHandler,
        "dump_artifacts",
        lambda name, **kwargs: dump_kwargs.append(kwargs),
    )
    monkeypatch.setattr(ResultHandler, "results", [])
    monkeypatch.delenv("ARCHI_ARGILLA", raising=False)

    stub = _RunStub()
    stub.run()

    assert calls.count("result_timestamp") == 1
    assert (
        calls[0] == "result_timestamp"
    ), "result_timestamp must be called before any _process_config"
    assert stub.result_basename == "bench-20260101_000000"
    assert dump_kwargs == [{"timestamp": "20260101_000000"}]
