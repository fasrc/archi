"""An ``inline+bundle`` arm writes its bundle first and then scores inline
(add-inline-bundle-judge-mode, D4, D6, D7).

Setup copied from ``tests/unit/test_benchmark_deferred_arm.py``: ``_process_config`` is
driven directly with a stub answerer, and ``run()`` with a stub ``handle_results``.
"""

from __future__ import annotations

import json

import pytest
import yaml

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import Benchmarker, ResultHandler
from src.utils.benchmark_resilience import OK
from src.utils.judge_bundle import (
    MODE_INLINE,
    MODE_INLINE_BUNDLE,
    JudgePlan,
    judge_status_of,
)

_CODE_VERSION = {"digest": "sha256:" + "a" * 64, "source": "content digest"}
_PACKAGE_DIGEST = "sha256:" + "b" * 64
_RAGAS_SETTINGS = {
    "enabled_metrics": ["answer_relevancy", "faithfulness"],
    "timeout": 600,
    "max_workers": 6,
    "batch_size": False,
    "embedding_model": "HuggingFace",
    "evaluator_model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
}
_BASENAME = "bench-20260101_000000"
_KEYS = ["question_1", "question_2", "question_3"]


def _plan(mode):
    return JudgePlan(
        mode=mode,
        arm_count=1,
        bundle_dir="judge_bundles",
        code_version=_CODE_VERSION,
        package_digest=_PACKAGE_DIGEST,
    )


def _make_bench(judge_plan):
    bench = object.__new__(Benchmarker)
    bench.config = {
        "services": {
            "benchmarking": {"mode_settings": {"ragas_settings": _RAGAS_SETTINGS}}
        },
        "global": {"verbosity": 0},
    }
    bench.benchmarking_configs = {"mode_settings": {"ragas_settings": _RAGAS_SETTINGS}}
    bench.judge_plan = judge_plan
    bench.result_basename = _BASENAME
    bench._arm_index = 0
    bench.required_fields = ["user_input"]
    bench.queries_to_answers = [{"user_input": f"question {i}"} for i in (1, 2, 3)]

    def _answer(question_item, question_id, modes_being_run):
        return {
            "q_results": {"question": question_item["user_input"], "status": OK},
            "dataset_result": {
                "user_input": question_item["user_input"],
                "retrieved_contexts": [f"full context {question_id} " + "x" * 2000],
                "response": f"answer {question_id}",
                "reference": f"reference {question_id}",
            },
            "matches": None,
        }

    bench._answer_and_score_question = _answer
    return bench


@pytest.fixture()
def out_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "OUTPUT_DIR", tmp_path)
    return tmp_path


def _bundle_folder(out_dir):
    return out_dir / "judge_bundles" / f"{_BASENAME}-arm0"


# --- _process_config (D4) ---------------------------------------------------------------


def test_the_arm_writes_the_bundle_and_then_scores_inline(out_dir):
    bench = _make_bench(_plan(MODE_INLINE_BUNDLE))
    calls = []

    def _ragas_results(rows, keys, results_by_key):
        # The bundle is complete before the judge is reached.
        assert (_bundle_folder(out_dir) / "BUNDLE_COMPLETE").exists()
        calls.append((len(rows), list(keys)))
        bench._ragas_scored = True
        return {"aggregate_faithfulness": 0.75, "faithfulness_scored": 3}

    bench.get_ragas_results = _ragas_results

    _, total = bench._process_config({"RAGAS"})

    assert calls == [(3, _KEYS)]
    assert total == {"aggregate_faithfulness": 0.75, "faithfulness_scored": 3}
    folder = _bundle_folder(out_dir)
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["judge_mode"] == "inline+bundle"
    assert manifest["judge_model"] == {
        "id": _RAGAS_SETTINGS["evaluator_model"],
        "revision": None,
    }
    assert manifest["enabled_metrics"] == ["answer_relevancy", "faithfulness"]
    lines = [json.loads(x) for x in (folder / "rows.jsonl").read_text().splitlines()]
    assert [line["key"] for line in lines] == _KEYS
    assert all(len(line["retrieved_contexts"][0]) > 2000 for line in lines)
    assert bench._judge_bundle == {
        "folder": folder.name,
        "digest": manifest["rows_sha256"],
    }


@pytest.mark.parametrize("mode", [MODE_INLINE, MODE_INLINE_BUNDLE])
def test_a_judge_failure_propagates_as_it_does_inline(out_dir, mode):
    bench = _make_bench(_plan(mode))

    def _judge_down(rows, keys, results_by_key):
        raise RuntimeError("the judge is down")

    bench.get_ragas_results = _judge_down

    with pytest.raises(RuntimeError, match="the judge is down"):
        bench._process_config({"RAGAS"})

    written = (_bundle_folder(out_dir) / "BUNDLE_COMPLETE").exists()
    assert written is (mode == MODE_INLINE_BUNDLE)


# --- handle_results (D6) -----------------------------------------------------------------


@pytest.fixture()
def _reset_results(monkeypatch):
    monkeypatch.setattr(ResultHandler, "results", [])
    monkeypatch.setattr(ResultHandler, "category_map_records_by_arm", [])
    monkeypatch.setattr(
        ResultHandler,
        "get_category_map",
        staticmethod(lambda _config: ([], "sha256:map")),
    )


_BUNDLE = {"folder": f"{_BASENAME}-arm0", "digest": "sha256:" + "c" * 64}


def _config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "benchmarking": {
                        "modes": ["RAGAS"],
                        "mode_settings": {"ragas_settings": dict(_RAGAS_SETTINGS)},
                    }
                }
            }
        )
    )
    return path


def test_handle_results_records_a_scored_arm_with_its_bundle(tmp_path, _reset_results):
    ResultHandler.handle_results(
        _config_file(tmp_path),
        {},
        {"aggregate_faithfulness": 0.75},
        running_config=None,
        modes_executed={"RAGAS"},
        ragas_scored=True,
        judge_bundle=dict(_BUNDLE),
        judge_mode=MODE_INLINE_BUNDLE,
    )

    record = ResultHandler.results[0]
    assert record["judge_bundle"] == _BUNDLE
    assert "judge_status" not in record
    assert judge_status_of(record) == "inline"
    assert record["total_results"] == {"aggregate_faithfulness": 0.75}


def test_handle_results_without_a_mode_still_records_pending(tmp_path, _reset_results):
    ResultHandler.handle_results(
        _config_file(tmp_path),
        {},
        {},
        running_config=None,
        modes_executed={"RAGAS"},
        ragas_scored=False,
        judge_bundle=dict(_BUNDLE),
    )

    assert ResultHandler.results[0]["judge_status"] == "pending"


# --- run() (D6, D7) ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode, expect_marker",
    [(MODE_INLINE_BUNDLE, False), (MODE_INLINE, False), ("deferred", True)],
)
def test_run_passes_the_mode_and_writes_the_marker_only_when_deferred(
    monkeypatch, tmp_path, mode, expect_marker
):
    handled = []

    class _RunStub(Benchmarker):
        def __init__(self):
            self.benchmarking_configs = {"modes": ["RAGAS"]}
            self.benchmark_name = "bench"
            self.queries_to_answers = []
            self.all_config_files = ["FINISHED"]
            self.current_config = "a.yaml"
            self.chain = None
            self.config = {}
            self.judge_plan = _plan(mode)

        def wait_for_ingestion_completion(self):
            return 0.0

        def _merge_anchor_questions(self):
            return None

        def _process_config(self, modes_being_run):
            self._ragas_scored = mode != "deferred"
            if mode != MODE_INLINE:
                self._judge_bundle = {
                    "folder": f"{self.result_basename}-arm0",
                    "digest": "sha256:0",
                }
            return {}, {}

        def load_new_configuration(self):
            self.current_config = self.all_config_files.pop(0)

    def _handle_results(config_path, results, total_results, **kwargs):
        handled.append((kwargs.get("judge_mode"), kwargs.get("judge_bundle")))
        ResultHandler.results.append(
            {
                "configuration": {"services": {"benchmarking": {"name": "arm0"}}},
                "single_question_results": {},
                "total_results": {},
                "ragas_effective_settings": None,
                "ragas_judge_identity_effective": None,
            }
        )

    monkeypatch.setattr(sb, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(ResultHandler, "result_timestamp", lambda: "20260101_000000")
    monkeypatch.setattr(ResultHandler, "check_collection", lambda cfg: None)
    monkeypatch.setattr(ResultHandler, "get_corpus_fingerprint", lambda cfg: None)
    monkeypatch.setattr(ResultHandler, "get_category_map", lambda cfg: (None, None))
    monkeypatch.setattr(ResultHandler, "handle_results", _handle_results)
    monkeypatch.setattr(ResultHandler, "add_metadata", lambda: None)
    monkeypatch.setattr(
        ResultHandler,
        "dump_artifacts",
        lambda name, **kwargs: (
            tmp_path / f"{name}-{kwargs['timestamp']}.json"
        ).write_text("{}"),
    )
    monkeypatch.setattr(ResultHandler, "results", [])
    monkeypatch.setattr(ResultHandler, "ab_comparison", {})
    monkeypatch.setattr(ResultHandler, "ab_comparisons", [])
    monkeypatch.setattr(ResultHandler, "leaderboard", {})
    monkeypatch.setattr(ResultHandler, "_corpus_snapshot_id", None)
    monkeypatch.delenv("ARCHI_ARGILLA", raising=False)

    _RunStub().run()

    expected_bundle = (
        None
        if mode == MODE_INLINE
        else {"folder": f"{_BASENAME}-arm0", "digest": "sha256:0"}
    )
    assert handled == [(mode, expected_bundle)]
    marker = tmp_path / f"{_BASENAME}.RESULT_COMPLETE"
    assert marker.exists() is expect_marker
