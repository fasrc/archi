"""A deferred arm writes a judge bundle in place of the judge (task 5.1).

Design refs: D6 (the call site in ``_process_config``), D8 (the pending arm
record and ``judge_status_of``).
"""

from __future__ import annotations

import json

import pytest
import yaml

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import Benchmarker, ResultHandler
from src.utils.benchmark_resilience import FAILED, OK
from src.utils.judge_bundle import JudgePlan, judge_status_of

_CODE_VERSION = {"digest": "sha256:" + "a" * 64, "source": "content digest"}
_PACKAGE_DIGEST = "sha256:" + "b" * 64
_RAGAS_SETTINGS = {
    "enabled_metrics": ["answer_relevancy", "faithfulness"],
    "timeout": 120,
    "max_workers": 4,
    "batch_size": False,
    "embedding_model": "OpenAI",
    "evaluator_model": "judge/model",
    "evaluator_model_revision": "0123abc",
}


def _plan(bundle_dir="judge_bundles"):
    return JudgePlan(
        mode="deferred",
        arm_count=2,
        bundle_dir=bundle_dir,
        code_version=_CODE_VERSION,
        package_digest=_PACKAGE_DIGEST,
    )


def _fail(*args, **kwargs):
    raise AssertionError("a deferred arm must not build or call the judge")


def _make_bench(judge_plan, *, all_fail=False):
    bench = object.__new__(Benchmarker)
    bench.config = {
        "services": {
            "benchmarking": {"mode_settings": {"ragas_settings": _RAGAS_SETTINGS}}
        },
        "global": {"verbosity": 0},
    }
    bench.benchmarking_configs = {"mode_settings": {"ragas_settings": _RAGAS_SETTINGS}}
    if judge_plan is not None:
        bench.judge_plan = judge_plan
    bench.result_basename = "bench-20260101_000000"
    bench._arm_index = 1
    bench.required_fields = ["user_input"]
    bench.queries_to_answers = [{"user_input": f"question {i}"} for i in (1, 2, 3)]
    bench.get_ragas_llm_evaluator = _fail
    bench.get_ragas_embedding_model = _fail

    def _answer(question_item, question_id, modes_being_run):
        if all_fail:
            return {
                "q_results": {
                    "question": question_item["user_input"],
                    "status": FAILED,
                },
                "dataset_result": None,
                "matches": None,
            }
        return {
            "q_results": {"question": question_item["user_input"], "status": OK},
            "dataset_result": {
                "user_input": question_item["user_input"],
                "retrieved_contexts": [f"context {question_id}"],
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


def test_deferred_arm_writes_one_bundle_and_no_aggregates(out_dir):
    bench = _make_bench(_plan())
    bench.get_ragas_results = _fail

    question_wise, total = bench._process_config({"RAGAS"})

    root = out_dir / "judge_bundles"
    assert [p.name for p in root.iterdir()] == ["bench-20260101_000000-arm1"]
    folder = root / "bench-20260101_000000-arm1"
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["arm_index"] == 1
    assert manifest["arm_count"] == 2
    assert manifest["result_basename"] == "bench-20260101_000000"
    lines = [json.loads(x) for x in (folder / "rows.jsonl").read_text().splitlines()]
    assert len(lines) == 3
    by_key = {line.pop("key"): line for line in lines}
    assert sorted(by_key) == ["question_1", "question_2", "question_3"]
    for i in (1, 2, 3):
        assert by_key[f"question_{i}"] == {
            "user_input": f"question {i}",
            "retrieved_contexts": [f"context {i}"],
            "response": f"answer {i}",
            "reference": f"reference {i}",
        }
    assert not any(k.startswith("aggregate_") for k in total)
    assert not any(k.endswith("_scored") for k in total)
    assert total == {}
    assert bench._ragas_scored is False
    assert bench._judge_bundle == {
        "folder": "bench-20260101_000000-arm1",
        "digest": manifest["rows_sha256"],
    }
    assert sorted(question_wise) == ["question_1", "question_2", "question_3"]


def test_deferred_arm_uses_the_plan_bundle_dir(out_dir):
    bench = _make_bench(_plan(bundle_dir="x"))
    bench.get_ragas_results = _fail

    bench._process_config({"RAGAS"})

    assert [p.name for p in out_dir.iterdir()] == ["x"]
    assert [p.name for p in (out_dir / "x").iterdir()] == ["bench-20260101_000000-arm1"]


def test_all_failed_deferred_arm_writes_an_empty_bundle_and_no_na(out_dir):
    bench = _make_bench(_plan(), all_fail=True)
    bench.get_ragas_results = _fail

    _, total = bench._process_config({"RAGAS"})

    folder = out_dir / "judge_bundles" / "bench-20260101_000000-arm1"
    assert (folder / "rows.jsonl").read_bytes() == b""
    assert json.loads((folder / "manifest.json").read_text())["row_count"] == 0
    assert (folder / "BUNDLE_COMPLETE").exists()
    assert total == {}
    assert bench._ragas_scored is False


@pytest.mark.parametrize(
    "judge_plan",
    [None, JudgePlan(mode="inline", arm_count=2)],
    ids=["absent", "inline"],
)
def test_inline_arm_calls_the_judge_and_writes_no_bundle(out_dir, judge_plan):
    bench = _make_bench(judge_plan)
    calls = []

    def _ragas_results(rows, keys, results_by_key):
        calls.append((len(rows), list(keys)))
        bench._ragas_scored = True
        return {"aggregate_faithfulness": 0.5}

    bench.get_ragas_results = _ragas_results

    _, total = bench._process_config({"RAGAS"})

    assert calls == [(3, ["question_1", "question_2", "question_3"])]
    assert total == {"aggregate_faithfulness": 0.5}
    assert list(out_dir.iterdir()) == []
    assert getattr(bench, "_judge_bundle", None) is None


# ---------------------------------------------------------------------------
# handle_results (D8). Setup copied from
# tests/unit/test_benchmark_report_records_running_config.py.
# ---------------------------------------------------------------------------


@pytest.fixture()
def _reset_results(monkeypatch):
    monkeypatch.setattr(ResultHandler, "results", [])
    monkeypatch.setattr(ResultHandler, "category_map_records_by_arm", [])
    monkeypatch.setattr(
        ResultHandler,
        "get_category_map",
        staticmethod(lambda _config: ([], "sha256:map")),
    )


def _write(tmp_path, config):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def _ragas_config(modes=("RAGAS",), **settings):
    return {
        "services": {
            "benchmarking": {
                "modes": list(modes),
                "mode_settings": {"ragas_settings": dict(settings)},
            }
        }
    }


_BUNDLE = {"folder": "bench-20260101_000000-arm0", "digest": "sha256:" + "c" * 64}


def test_handle_results_records_a_pending_arm(tmp_path, _reset_results):
    ResultHandler.handle_results(
        _write(tmp_path, _ragas_config(timeout=600, max_workers=4)),
        {},
        {},
        running_config=None,
        modes_executed={"RAGAS"},
        ragas_scored=False,
        judge_bundle=dict(_BUNDLE),
    )

    record = ResultHandler.results[0]
    assert record["judge_status"] == "pending"
    assert record["judge_bundle"] == _BUNDLE
    assert record["ragas_effective_settings"] is None
    assert record["ragas_judge_identity_effective"] is None
    assert judge_status_of(record) == "pending"


def test_handle_results_inline_arm_gets_no_new_key(tmp_path, _reset_results):
    ResultHandler.handle_results(
        _write(tmp_path, _ragas_config(timeout=600, max_workers=4)),
        {},
        {},
        running_config=None,
        modes_executed={"RAGAS"},
        ragas_scored=False,
    )

    record = ResultHandler.results[0]
    assert "judge_status" not in record
    assert "judge_bundle" not in record
    assert judge_status_of(record) == "inline"


# ---------------------------------------------------------------------------
# judge_status_of (D8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "record, expected",
    [
        ({}, "inline"),
        ({"total_results": {}}, "inline"),
        ({"judge_status": "pending"}, "pending"),
        ({"judge_status": "scored"}, "scored"),
    ],
)
def test_judge_status_of(record, expected):
    assert judge_status_of(record) == expected


# ---------------------------------------------------------------------------
# A two-arm deferred run() (D6)
# ---------------------------------------------------------------------------


def test_two_arm_deferred_run_passes_arm_index_and_bundle(monkeypatch, tmp_path):
    seen_arms = []
    handled = []

    class _RunStub(Benchmarker):
        def __init__(self):
            self.benchmarking_configs = {"modes": ["RAGAS"]}
            self.benchmark_name = "bench"
            self.queries_to_answers = []
            self.all_config_files = ["b.yaml", "FINISHED"]
            self.current_config = "a.yaml"
            self.chain = None
            self.config = {}
            self.judge_plan = _plan()

        def wait_for_ingestion_completion(self):
            return 0.0

        def _merge_anchor_questions(self):
            return None

        def _process_config(self, modes_being_run):
            seen_arms.append(self._arm_index)
            assert self._judge_bundle is None
            self._ragas_scored = False
            self._judge_bundle = {
                "folder": f"{self.result_basename}-arm{self._arm_index}",
                "digest": f"sha256:{self._arm_index}",
            }
            return {}, {}

        def load_new_configuration(self):
            self.current_config = self.all_config_files.pop(0)

    def _handle_results(config_path, results, total_results, **kwargs):
        handled.append(kwargs.get("judge_bundle"))
        ResultHandler.results.append(
            {
                "configuration": {
                    "services": {"benchmarking": {"name": f"arm{len(handled)}"}}
                },
                "single_question_results": {
                    "question_1": {"question": "q", "answer": "a", "status": OK}
                },
                "total_results": {},
                "judge_status": "pending",
                "judge_bundle": kwargs.get("judge_bundle"),
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
    monkeypatch.setattr(ResultHandler, "dump_artifacts", lambda name, **kwargs: None)
    monkeypatch.setattr(ResultHandler, "results", [])
    monkeypatch.setattr(ResultHandler, "ab_comparison", {})
    monkeypatch.setattr(ResultHandler, "ab_comparisons", [])
    monkeypatch.setattr(ResultHandler, "leaderboard", {})
    monkeypatch.setattr(ResultHandler, "_corpus_snapshot_id", None)
    monkeypatch.delenv("ARCHI_ARGILLA", raising=False)

    _RunStub().run()

    assert seen_arms == [0, 1]
    assert handled == [
        {"folder": "bench-20260101_000000-arm0", "digest": "sha256:0"},
        {"folder": "bench-20260101_000000-arm1", "digest": "sha256:1"},
    ]
    assert len(ResultHandler.ab_comparisons) == 1
    rows = ResultHandler.leaderboard["rows"]
    assert len(rows) == 2
    for row in rows:
        assert set(row["metrics"].values()) == {None}
        assert row["incomplete"] is True
