"""Tests that run() fixes the result basename BEFORE waiting for ingestion (design D4).

The result timestamp must be the FIRST statement of run() so the file name
records when the benchmark started, not when the ingest wait finished.
"""

from __future__ import annotations

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import Benchmarker, ResultHandler


def test_result_timestamp_before_wait_for_ingestion(monkeypatch):
    calls = []

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
            calls.append("wait_for_ingestion")
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
        lambda name, **kwargs: None,
    )
    monkeypatch.setattr(ResultHandler, "results", [])
    monkeypatch.delenv("ARCHI_ARGILLA", raising=False)

    stub = _RunStub()
    stub.run()

    assert calls[:2] == ["result_timestamp", "wait_for_ingestion"]
    assert calls.count("result_timestamp") == 1
