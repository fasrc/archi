"""The merge of judged bundles into one judged result (#732, task 3.7, design D8).

The bundles are written with the PR 2 writer and scored by the offline scorer
with the stub judge; the pending result has the per-arm fields that
``handle_results`` writes for a deferred arm.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.bin.service_benchmark import ResultHandler
from src.utils.judge_scorer import score_bundle
from tests.unit.test_judge_scorer import (
    METRICS,
    StubJudge,
    load_queue,
    make_bundle,
    make_ctx,
)
from tests.unit.test_ragas_scoring_characterization import _install_ragas_stub

REPO = Path(__file__).resolve().parents[2]
MERGE_PATH = REPO / "scripts" / "benchmarking" / "judge" / "merge.py"
BASENAME = "goldenset-20261023_120000"
FILLED = (
    "judge_status",
    "ragas_effective_settings",
    "ragas_judge_identity_effective",
    "judge_execution",
    "single_question_results",
    "total_results",
)


@pytest.fixture
def merge():
    spec = importlib.util.spec_from_file_location("judge_merge", MERGE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def jq(monkeypatch):
    module = load_queue()
    monkeypatch.setattr(module, "time_left", lambda job_id: None)
    return module


@pytest.fixture(autouse=True)
def _stub_judge(monkeypatch):
    stub = StubJudge()
    _install_ragas_stub(monkeypatch, stub.evaluate)
    return stub


@pytest.fixture(autouse=True)
def _keep_result_handler():
    saved = (
        ResultHandler.results,
        ResultHandler.ab_comparison,
        ResultHandler.ab_comparisons,
        ResultHandler.leaderboard,
        ResultHandler._corpus_snapshot_id,
    )
    yield
    (
        ResultHandler.results,
        ResultHandler.ab_comparison,
        ResultHandler.ab_comparisons,
        ResultHandler.leaderboard,
        ResultHandler._corpus_snapshot_id,
    ) = saved


def pending_arm(index, folder, digest):
    return {
        "single_question_results": {
            f"question_{n}": {
                "question": f"q{n}",
                "answer": f"answer {n}",
                "reference_answer": "" if n == 3 else f"reference {n}",
                "time_elapsed": 1.0,
                "sources_metadata": [],
                "messages": [],
            }
            for n in range(1, 7)
        },
        "total_results": {"relative_source_accuracy": 0.5},
        "configuration_file": f"/root/archi/configs/arm{index}.yaml",
        "configuration": {
            "services": {
                "benchmarking": {
                    "name": f"arm{index}",
                    "agent_md_file": f"prompts/arm{index}.md",
                    "mode_settings": {"ragas_settings": {"enabled_metrics": METRICS}},
                }
            }
        },
        "corpus_fingerprint": "sha256:corpus",
        "corpus_unchanged_at_endpoints": True,
        "embedding_tags_unchanged_at_endpoints": True,
        "configuration_divergence": [],
        "agent_md_sha256": f"sha256:agent{index}",
        "ragas_effective_settings": None,
        "judge_usage": None,
        "ragas_judge_identity_effective": None,
        "judge_status": "pending",
        "judge_bundle": {"folder": folder, "digest": digest},
    }


class Run:
    """A deferred two-arm run: its queue, bundles and pending result."""

    def __init__(self, tmp_path, jq, *, arms=2, publish=True):
        self.tmp_path = tmp_path
        self.jq = jq
        self.queue = tmp_path / "queue"
        self.queue.mkdir()
        self.results = self.queue / "results"
        self.results.mkdir()
        self.bundles = []
        records = []
        for arm in range(arms):
            bundle = make_bundle(
                self.queue, BASENAME, arm=arm, arm_count=arms, count=6, drafts=(3,)
            )
            digest = json.loads((bundle / "manifest.json").read_text())["rows_sha256"]
            records.append(pending_arm(arm, bundle.name, digest))
            self.bundles.append(bundle)
        self.pending = {
            "benchmarking_results": records,
            "metadata": {"corpus_snapshot_id": "snap-1", "time": "2026-10-23"},
            "leaderboard": {"primary_metric": "answer_relevancy", "rows": []},
        }
        self.pending_path = self.results / f"{BASENAME}.json"
        if publish:
            self.publish()

    def publish(self):
        self.pending_path.write_text(json.dumps(self.pending, indent=4))

    def score(self, arm, **env):
        ctx = make_ctx(self.tmp_path, self.queue, self.jq, env=env or None)
        assert score_bundle(self.bundles[arm], ctx) == "SCORED"

    @property
    def judged_path(self):
        return self.results / f"{BASENAME}.judged.json"

    def judged(self):
        return json.loads(self.judged_path.read_text())


@pytest.fixture
def run(tmp_path, jq):
    return Run(tmp_path, jq)


def _main(merge, run):
    return merge.main(["--queue", str(run.queue)])


def test_the_merge_waits_for_every_arm_and_then_writes_one_file(merge, run, capsys):
    run.score(0)
    assert _main(merge, run) == 0
    assert not run.judged_path.exists()
    assert "arm 1" in capsys.readouterr().out
    run.score(1)
    assert _main(merge, run) == 0
    judged = run.judged()
    assert [arm["judge_status"] for arm in judged["benchmarking_results"]] == [
        "scored",
        "scored",
    ]
    assert all((bundle / "MERGED").exists() for bundle in run.bundles)


def test_a_failed_arm_writes_nothing_and_exits_non_zero(merge, run, capsys):
    run.score(0)
    (run.bundles[1] / "FAILED").write_text("rows.jsonl has digest x\n")
    assert _main(merge, run) == 1
    assert not run.judged_path.exists()
    assert "arm 1" in capsys.readouterr().err
    assert not (run.bundles[0] / "MERGED").exists()


def test_a_result_not_yet_published_gets_nothing(merge, tmp_path, jq, capsys):
    run = Run(tmp_path, jq, publish=False)
    run.score(0)
    run.score(1)
    assert _main(merge, run) == 0
    assert not run.judged_path.exists()
    assert "not yet published" in capsys.readouterr().out


def test_arms_of_different_identities_keep_scores_and_withhold_ranks(merge, run):
    run.score(0, JUDGE_PRECISION="bf16")
    run.score(1, JUDGE_PRECISION="fp8")
    assert _main(merge, run) == 0
    judged = run.judged()
    arms = judged["benchmarking_results"]
    assert [a["ragas_judge_identity_effective"]["precision"] for a in arms] == [
        "bf16",
        "fp8",
    ]
    for arm in arms:
        assert arm["total_results"]["aggregate_answer_relevancy"] is not None
    assert judged["leaderboard"]["comparable"] is False
    assert [row["rank"] for row in judged["leaderboard"]["rows"]] == [None, None]
    assert judged["ab_comparison"]["comparable"] is False
    assert judged["ab_comparison"]["aggregate"]["wins_a"] is None
    assert "cross_arm_ragas" not in json.dumps(judged)


def test_arms_of_one_identity_are_ranked(merge, run):
    run.score(0)
    run.score(1)
    assert _main(merge, run) == 0
    judged = run.judged()
    assert judged["leaderboard"]["comparable"] is True
    assert judged["leaderboard"]["primary_metric"] == "answer_relevancy"
    assert judged["leaderboard"]["shared_context"]["corpus_snapshot_id"] == "snap-1"
    assert judged["ab_comparison"]["comparable"] is True


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_the_judged_file_fills_the_judge_fields_and_keeps_every_other_field(merge, run):
    run.score(0)
    run.score(1)
    assert _main(merge, run) == 0
    judged = run.judged()
    for index, arm in enumerate(judged["benchmarking_results"]):
        scores = json.loads((run.bundles[index] / "judge_scores.json").read_text())
        pending = run.pending["benchmarking_results"][index]
        assert arm["judge_status"] == "scored"
        assert arm["ragas_effective_settings"] == {"timeout": 600, "max_workers": 6}
        assert (
            arm["ragas_judge_identity_effective"]
            == scores["ragas_judge_identity_effective"]
        )
        assert arm["judge_execution"] == scores["judge_execution"]
        for name, value in pending.items():
            if name not in FILLED:
                assert arm[name] == value, name
        assert arm["judge_bundle"] == pending["judge_bundle"]
        assert arm["total_results"]["relative_source_accuracy"] == 0.5
        assert arm["total_results"]["answer_relevancy_scored"] == "6 of 6"
        assert arm["total_results"]["context_recall_scored"] == "5 of 6"
        question = arm["single_question_results"]["question_1"]
        assert (
            question["answer_relevancy"]
            == scores["scores"]["question_1"]["answer_relevancy"]
        )
        assert question["answer"] == "answer 1"
        assert "context_recall" not in arm["single_question_results"]["question_3"]
    assert judged["metadata"] == run.pending["metadata"]
    # The identity lives under ragas_judge_identity_effective only. (The
    # leaderboard's shared_context has had a `judge_identity` summary since
    # #618; that is not a result key.)
    assert "judge_identity" not in judged
    for arm in judged["benchmarking_results"]:
        assert "judge_identity" not in set(_keys(arm))


def test_the_pending_file_is_unchanged_and_a_second_merge_gives_the_same_bytes(
    merge, run
):
    run.score(0)
    run.score(1)
    before = run.pending_path.read_bytes()
    assert _main(merge, run) == 0
    first = run.judged_path.read_bytes()
    assert _main(merge, run) == 0
    assert run.judged_path.read_bytes() == first
    assert run.pending_path.read_bytes() == before
    run.judged_path.unlink()
    for bundle in run.bundles:
        (bundle / "MERGED").unlink()
    assert _main(merge, run) == 0
    assert run.judged_path.read_bytes() == first


def test_the_judged_file_is_sorted_and_strict_json(merge, run):
    run.score(0)
    run.score(1)
    assert _main(merge, run) == 0
    data = run.judged_path.read_text()
    assert "NaN" not in data
    judged = json.loads(data)
    assert list(judged) == sorted(judged)


def test_a_digest_mismatch_exits_non_zero(merge, run, capsys):
    run.score(0)
    run.score(1)
    run.pending["benchmarking_results"][0]["judge_bundle"]["digest"] = "sha256:x"
    run.publish()
    assert _main(merge, run) == 1
    assert not run.judged_path.exists()
    assert "digest" in capsys.readouterr().err
    assert not (run.bundles[0] / "MERGED").exists()


def test_a_killed_merge_leaves_a_temporary_file_the_next_merge_removes_later(
    merge, run
):
    run.score(0)
    run.score(1)
    leftover = run.results / ".tmp.node.4242.0123456789abcdef"
    leftover.write_bytes(b'{"benchmarking_results": [')
    assert _main(merge, run) == 0
    assert run.judged_path.exists() and leftover.exists()
    old = time.time() - 2 * 3600
    os.utime(leftover, (old, old))
    assert _main(merge, run) == 0
    assert not leftover.exists()


def test_an_existing_judged_file_with_other_bytes_is_never_replaced(merge, run, capsys):
    run.score(0)
    run.score(1)
    run.judged_path.write_bytes(b'{"written": "by another merge"}')
    assert _main(merge, run) == 1
    assert run.judged_path.read_bytes() == b'{"written": "by another merge"}'
    assert "differ" in capsys.readouterr().err
    assert not any((bundle / "MERGED").exists() for bundle in run.bundles)


def test_exactly_one_of_two_different_publications_wins(merge, tmp_path):
    path = tmp_path / "x.judged.json"
    assert merge.publish_judged(path, b'{"a": 1}') == "written"
    assert merge.publish_judged(path, b'{"b": 2}') == "conflict"
    assert merge.publish_judged(path, b'{"a": 1}') == "same"
    assert path.read_bytes() == b'{"a": 1}'


def test_a_question_key_missing_from_the_pending_result_is_refused(merge, run, capsys):
    run.score(0)
    run.score(1)
    del run.pending["benchmarking_results"][1]["single_question_results"]["question_2"]
    run.publish()
    assert _main(merge, run) == 1
    assert "question_2" in capsys.readouterr().err
    assert not run.judged_path.exists()


def test_a_scores_file_without_a_judge_field_is_refused_not_a_crash(merge, run, capsys):
    run.score(0)
    run.score(1)
    path = run.bundles[1] / "judge_scores.json"
    record = json.loads(path.read_text())
    del record["judge_execution"]
    path.write_text(json.dumps(record))
    assert _main(merge, run) == 1
    assert "judge_execution" in capsys.readouterr().err
    assert not run.judged_path.exists()


def test_an_arm_count_that_differs_from_the_pending_result_is_refused(
    merge, run, capsys
):
    run.score(0)
    run.score(1)
    run.pending["benchmarking_results"].pop()
    run.publish()
    assert _main(merge, run) == 1
    assert "arm" in capsys.readouterr().err


def test_two_concurrent_merges_leave_one_complete_file(run):
    run.score(0)
    run.score(1)
    command = [sys.executable, str(MERGE_PATH), "--queue", str(run.queue)]
    procs = [
        subprocess.Popen(
            command, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        for _ in range(2)
    ]
    codes = [proc.wait(timeout=120) for proc in procs]
    assert codes == [0, 0], [proc.stderr.read() for proc in procs]
    json.loads(run.judged_path.read_text())
    assert all((bundle / "MERGED").exists() for bundle in run.bundles)
    leftovers = [p for p in run.results.iterdir() if p.name.startswith(".tmp.")]
    assert leftovers == []


def test_the_merge_does_not_change_the_live_result_handler(merge, run):
    run.score(0)
    run.score(1)
    ResultHandler.results = ["live"]
    assert _main(merge, run) == 0
    assert ResultHandler.results == ["live"]


def test_run_builds_its_comparisons_with_the_merges_method(monkeypatch):
    """run() and the merge share ResultHandler.build_comparisons."""
    from src.bin.service_benchmark import Benchmarker

    metrics_seen = []
    real = ResultHandler.build_comparisons

    def spy(primary_metric):
        metrics_seen.append(primary_metric)
        real(primary_metric)

    class _RunStub(Benchmarker):
        def __init__(self):
            self.benchmarking_configs = {"modes": ["RAGAS"]}
            self.benchmark_name = "t"
            self.queries_to_answers = []
            self.all_config_files = ["a.yaml", "b.yaml"]
            self.current_config = "a.yaml"
            self.chain = None
            self.config = {
                "services": {"benchmarking": {"primary_metric": "faithfulness"}}
            }

        def wait_for_ingestion_completion(self):
            return 0.0

        def _merge_anchor_questions(self):
            return None

        def _process_config(self, modes_being_run):
            return {}, {}

        def load_new_configuration(self):
            self.all_config_files.pop(0)

    def handle_results(config_path, *args, **kwargs):
        ResultHandler.results.append(
            {
                "single_question_results": {},
                "total_results": {},
                "configuration": {
                    "services": {"benchmarking": {"name": str(config_path)}}
                },
            }
        )

    monkeypatch.setattr(ResultHandler, "check_collection", lambda cfg: None)
    monkeypatch.setattr(ResultHandler, "get_corpus_fingerprint", lambda cfg: None)
    monkeypatch.setattr(ResultHandler, "get_category_map", lambda cfg: (None, None))
    monkeypatch.setattr(ResultHandler, "handle_results", handle_results)
    monkeypatch.setattr(ResultHandler, "add_metadata", lambda: None)
    monkeypatch.setattr(ResultHandler, "dump_artifacts", lambda name, **kwargs: None)
    monkeypatch.setattr(ResultHandler, "results", [])
    monkeypatch.setattr(ResultHandler, "ab_comparisons", [])
    monkeypatch.setattr(ResultHandler, "build_comparisons", staticmethod(spy))
    monkeypatch.delenv("ARCHI_ARGILLA", raising=False)

    _RunStub().run()

    assert metrics_seen == ["faithfulness"]
    assert len(ResultHandler.ab_comparisons) == 1
    assert ResultHandler.leaderboard["primary_metric"] == "faithfulness"


def test_merge_result_does_not_change_its_input(merge, run):
    run.score(0)
    run.score(1)
    pending = copy.deepcopy(run.pending)
    scores = [
        json.loads((bundle / "judge_scores.json").read_text()) for bundle in run.bundles
    ]
    merge.merge_result(pending, scores)
    assert pending == run.pending
