"""The offline judge scorer (#732, tasks 3.1b, 3.2, 3.3, 3.4, 3.5, 3.6, 3.6a).

Bundles are written with the PR 2 writer (``write_judge_bundle``), ragas is the
``sys.modules`` stub of the characterization tests, and the queue is a temporary
folder. No test needs a GPU, Slurm or the network.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import types
from pathlib import Path

import pandas as pd
import pytest

from src.utils.judge_bundle import write_judge_bundle
from src.utils.judge_identity import IDENTITY_FIELDS
from src.utils.judge_scorer import (
    JobContext,
    preflight,
    run_job,
    score_bundle,
)
from src.utils.ragas_scoring import RagasScoreOutcome, score_ragas
from tests.unit.test_ragas_scoring_characterization import _install_ragas_stub

QUEUE_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "benchmarking"
    / "judge"
    / "judge_queue.py"
)
MODEL = "meta-llama/Llama-3.3-70B-Instruct"
REVISION = "6f6073b4"
CODE = "sha256:code"
PACKAGE = "sha256:pkg"
METRICS = ["answer_relevancy", "context_recall"]


def load_queue():
    spec = importlib.util.spec_from_file_location("judge_queue_for_scorer", QUEUE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score_of(metric, row):
    """Fixed scores; every row whose number ends in 7 gets NaN."""
    number = int(row["user_input"][1:])
    if number % 10 == 7:
        return math.nan
    return (number % 10) / 10 + (0.01 if metric == "context_recall" else 0.0)


class StubJudge:
    """The ragas ``evaluate`` of the stub; ``fail_after`` calls succeed, then it raises."""

    def __init__(self, fail_after=None, on_call=None):
        self.fail_after = fail_after
        self.on_call = on_call
        self.calls = []

    def evaluate(self, dataset, metrics=None, **kwargs):
        if self.on_call is not None:
            self.on_call()
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("the judge server stopped")
        metric = metrics[0].name
        self.calls.append(
            {
                "metric": metric,
                "users": [row["user_input"] for row in dataset],
                "kwargs": kwargs,
            }
        )
        scores = [score_of(metric, row) for row in dataset]
        return types.SimpleNamespace(to_pandas=lambda: pd.DataFrame({metric: scores}))

    def rows_scored(self):
        return sum(len(call["users"]) for call in self.calls)


class OpenAIEmbeddings:
    model = "text-embedding-ada-002"


def make_rows(count, drafts=()):
    rows = [
        {
            "user_input": f"q{i}",
            "retrieved_contexts": [f"context {i}"],
            "response": f"answer {i}",
            "reference": "" if i in drafts else f"reference {i}",
        }
        for i in range(1, count + 1)
    ]
    keys = [f"question_{i}" for i in range(1, count + 1)]
    return rows, keys


def ragas_settings(**overrides):
    settings = {
        "enabled_metrics": list(METRICS),
        "timeout": 600,
        "max_workers": 6,
        "batch_size": None,
        "embedding_model": "openai",
        "evaluator_model": MODEL,
        "evaluator_model_revision": REVISION,
    }
    settings.update(overrides)
    return settings


def make_bundle(
    queue,
    basename="run-20261023_120000",
    *,
    arm=0,
    arm_count=1,
    count=12,
    drafts=(3,),
    settings=None,
    package=PACKAGE,
    ready=True,
):
    rows, keys = make_rows(count, drafts)
    settings = settings or ragas_settings()
    info = write_judge_bundle(
        queue,
        result_basename=basename,
        arm_index=arm,
        arm_count=arm_count,
        rows=rows,
        keys=keys,
        enabled_metrics=settings["enabled_metrics"],
        ragas_settings=settings,
        verbosity=0,
        code_version={"digest": CODE},
        package_digest=package,
    )
    folder = Path(queue) / info["folder"]
    if ready:
        (folder / "READY").write_bytes(b"")
    return folder


def write_judge_env(folder, **overrides):
    values = {
        "JUDGE_MODEL_ID": MODEL,
        "JUDGE_MODEL_REVISION": REVISION,
        "JUDGE_PRECISION": "bf16",
        "JUDGE_BOOTSTRAP_ROWS_PER_MINUTE": "2",
    }
    values.update(overrides)
    path = Path(folder) / "judge.env"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    return path


def write_sidecar(folder, code=CODE, package=PACKAGE, name="scorer-identity.json"):
    path = Path(folder) / name
    path.write_text(
        json.dumps(
            {
                "code_digest": code,
                "package_digest": package,
                "image_digest": "sha256:scorer-image",
                "ragas_version": "0.3.5",
            },
            sort_keys=True,
        )
    )
    return path


@pytest.fixture
def jq(monkeypatch):
    module = load_queue()
    monkeypatch.setattr(module, "time_left", lambda job_id: None)
    return module


@pytest.fixture
def queue(tmp_path):
    folder = tmp_path / "queue"
    folder.mkdir()
    return folder


@pytest.fixture
def judge(monkeypatch):
    stub = StubJudge()
    _install_ragas_stub(monkeypatch, lambda *a, **kw: stub.evaluate(*a, **kw))
    return stub


def make_ctx(tmp_path, queue, jq, *, job_id="1001", live=None, env=None, **kw):
    judge_built = kw.pop("judge_built", [])

    def judge_factory():
        judge_built.append(True)
        return types.SimpleNamespace(temperature=0.0, top_p=None, max_tokens=None)

    env_path = write_judge_env(tmp_path, **(env or {}))
    sidecar = jq.read_sidecar(kw.pop("sidecar_path", None) or write_sidecar(tmp_path))
    return JobContext(
        queue=queue,
        job_id=job_id,
        judge_env=jq.read_judge_env(env_path),
        sidecar=sidecar,
        live=live or {"code_digest": CODE, "package_digest": PACKAGE},
        judge_factory=judge_factory,
        embeddings_factory=lambda name: OpenAIEmbeddings(),
        queue_api=jq,
        node="holygpu8a12345",
        now=lambda: "2026-10-23T20:00:00+00:00",
        **kw,
    )


def scores_file(bundle):
    found = list(Path(bundle).glob("scores.*.partial.jsonl"))
    assert len(found) == 1, found
    return found[0]


def judge_scores(bundle):
    return json.loads((Path(bundle) / "judge_scores.json").read_text())


# --- 3.3 checkpoints ------------------------------------------------------------


def test_a_restart_after_40_of_109_rows_scores_only_the_other_69(
    tmp_path, queue, jq, monkeypatch
):
    settings = ragas_settings(enabled_metrics=["answer_relevancy"])
    bundle = make_bundle(queue, count=109, drafts=(), settings=settings)
    first = StubJudge(fail_after=5)
    _install_ragas_stub(monkeypatch, first.evaluate)
    ctx = make_ctx(tmp_path, queue, jq, chunk_size=8)
    with pytest.raises(RuntimeError):
        score_bundle(bundle, ctx)
    data = scores_file(bundle).read_bytes()
    assert data.endswith(b"\n") and data.count(b"\n") == 40
    assert not (bundle / "SCORED").exists()

    second = StubJudge()
    _install_ragas_stub(monkeypatch, second.evaluate)
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq, chunk_size=8)) == "SCORED"
    assert second.rows_scored() == 69
    assert all(len(call["users"]) <= 8 for call in second.calls)
    scores = judge_scores(bundle)["scores"]
    assert sorted(scores) == sorted(f"question_{i}" for i in range(1, 110))
    lines = [json.loads(line) for line in scores_file(bundle).read_text().splitlines()]
    assert len({(r["key"], r["metric"]) for r in lines}) == len(lines) == 109
    assert (bundle / "SCORED").exists()


def _first_pass(tmp_path, queue, jq, monkeypatch, *, chunks, **ctx_kw):
    settings = ragas_settings(enabled_metrics=["answer_relevancy"])
    bundle = make_bundle(queue, count=40, drafts=(), settings=settings)
    stub = StubJudge(fail_after=chunks)
    _install_ragas_stub(monkeypatch, stub.evaluate)
    with pytest.raises(RuntimeError):
        score_bundle(bundle, make_ctx(tmp_path, queue, jq, chunk_size=8, **ctx_kw))
    return bundle


def _second_pass(tmp_path, queue, jq, monkeypatch, bundle, **ctx_kw):
    stub = StubJudge()
    _install_ragas_stub(monkeypatch, stub.evaluate)
    result = score_bundle(bundle, make_ctx(tmp_path, queue, jq, chunk_size=8, **ctx_kw))
    return result, stub


def test_a_torn_last_line_is_truncated_and_its_pair_scored_again(
    tmp_path, queue, jq, monkeypatch
):
    bundle = _first_pass(tmp_path, queue, jq, monkeypatch, chunks=1)
    path = scores_file(bundle)
    with open(path, "ab") as handle:
        handle.write(b'{"key": "question_9", "metric": "answer_rel')
    result, stub = _second_pass(tmp_path, queue, jq, monkeypatch, bundle)
    assert result == "SCORED"
    assert stub.rows_scored() == 32
    assert "q9" in stub.calls[0]["users"]
    for line in path.read_text().splitlines():
        json.loads(line)


def test_an_invalid_last_line_with_a_newline_is_a_torn_write(
    tmp_path, queue, jq, monkeypatch
):
    bundle = _first_pass(tmp_path, queue, jq, monkeypatch, chunks=1)
    with open(scores_file(bundle), "ab") as handle:
        handle.write(b'{"key": "question_9", "metr\n')
    result, stub = _second_pass(tmp_path, queue, jq, monkeypatch, bundle)
    assert result == "SCORED"
    assert stub.rows_scored() == 32


def test_an_invalid_line_that_is_not_the_last_fails_the_bundle(
    tmp_path, queue, jq, monkeypatch
):
    bundle = _first_pass(tmp_path, queue, jq, monkeypatch, chunks=2)
    path = scores_file(bundle)
    lines = path.read_text().splitlines(keepends=True)
    lines[2] = "garbage\n"
    path.write_text("".join(lines))
    result, stub = _second_pass(tmp_path, queue, jq, monkeypatch, bundle)
    assert result == "FAILED"
    assert "line 3" in (bundle / "FAILED").read_text()
    assert stub.calls == []
    assert not (bundle / "SCORED").exists()


def test_a_repeated_pair_uses_its_first_record(tmp_path, queue, jq, monkeypatch):
    bundle = _first_pass(tmp_path, queue, jq, monkeypatch, chunks=1)
    path = scores_file(bundle)
    first = json.loads(path.read_text().splitlines()[0])
    with open(path, "ab") as handle:
        duplicate = dict(first, value=0.99)
        handle.write((json.dumps(duplicate) + "\n").encode())
    result, _ = _second_pass(tmp_path, queue, jq, monkeypatch, bundle)
    assert result == "SCORED"
    assert judge_scores(bundle)["scores"][first["key"]]["answer_relevancy"] == (
        first["value"]
    )


def test_another_identity_never_reads_this_identitys_checkpoint(
    tmp_path, queue, jq, monkeypatch
):
    bundle = _first_pass(tmp_path, queue, jq, monkeypatch, chunks=3)
    bf16_file = scores_file(bundle)
    # Poison the bf16 scores: an fp8 job that read them would publish 0.5.
    poisoned = [
        json.dumps(dict(json.loads(line), value=0.5)) + "\n"
        for line in bf16_file.read_text().splitlines()
    ]
    bf16_file.write_text("".join(poisoned))
    result, stub = _second_pass(
        tmp_path, queue, jq, monkeypatch, bundle, env={"JUDGE_PRECISION": "fp8"}
    )
    assert result == "SCORED"
    assert stub.rows_scored() == 40
    assert len(list(bundle.glob("scores.*.partial.jsonl"))) == 2
    result_json = judge_scores(bundle)
    assert result_json["ragas_judge_identity_effective"]["precision"] == "fp8"
    assert result_json["scores"]["question_1"]["answer_relevancy"] == 0.1


# --- 3.4 one bundle's failure ---------------------------------------------------


def test_a_bundle_whose_digest_does_not_match_fails_and_the_others_score(
    tmp_path, queue, jq, judge
):
    first = make_bundle(queue, "a-20261023_120000")
    broken = make_bundle(queue, "b-20261023_120000")
    third = make_bundle(queue, "c-20261023_120000")
    with open(broken / "rows.jsonl", "ab") as handle:
        handle.write(b"\n")
    assert run_job(make_ctx(tmp_path, queue, jq)) == 0
    assert (first / "SCORED").exists() and (third / "SCORED").exists()
    reason = (broken / "FAILED").read_text()
    assert "rows.jsonl" in reason and "sha256:" in reason
    assert not (broken / "SCORED").exists()


def test_an_unreadable_manifest_fails_the_bundle(tmp_path, queue, jq, judge):
    bundle = make_bundle(queue)
    (bundle / "manifest.json").write_text("{")
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "FAILED"
    assert "manifest.json" in (bundle / "FAILED").read_text()


def test_a_manifest_without_the_scorer_keys_fails_the_bundle(
    tmp_path, queue, jq, judge
):
    bundle = make_bundle(queue)
    manifest = json.loads((bundle / "manifest.json").read_text())
    del manifest["ragas_run_config_kwargs"]
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "FAILED"
    assert "ragas_run_config_kwargs" in (bundle / "FAILED").read_text()


def test_a_missing_rows_file_fails_the_bundle(tmp_path, queue, jq, judge):
    bundle = make_bundle(queue)
    (bundle / "rows.jsonl").unlink()
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "FAILED"
    assert "rows.jsonl cannot be read" in (bundle / "FAILED").read_text()


def test_a_score_that_is_not_a_number_is_recorded_with_its_error(
    tmp_path, queue, jq, monkeypatch
):
    def evaluate(dataset, metrics=None, **kwargs):
        name = metrics[0].name
        cells = [None] + [0.5] * (len(dataset) - 1)
        return types.SimpleNamespace(to_pandas=lambda: pd.DataFrame({name: cells}))

    _install_ragas_stub(monkeypatch, evaluate)
    settings = ragas_settings(enabled_metrics=["answer_relevancy"])
    bundle = make_bundle(queue, count=3, drafts=(), settings=settings)
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "SCORED"
    first = json.loads(scores_file(bundle).read_text().splitlines()[0])
    assert first["value"] is None and "the judge returned" in first["error"]
    assert judge_scores(bundle)["nan_counts"] == {"answer_relevancy": 1}


def test_a_judge_that_returns_the_wrong_number_of_scores_stops_the_job(
    tmp_path, queue, jq, monkeypatch
):
    def evaluate(dataset, metrics=None, **kwargs):
        name = metrics[0].name
        return types.SimpleNamespace(to_pandas=lambda: pd.DataFrame({name: [0.5]}))

    _install_ragas_stub(monkeypatch, evaluate)
    bundle = make_bundle(queue, count=3, drafts=())
    with pytest.raises(RuntimeError, match="1 scores for 3 rows"):
        score_bundle(bundle, make_ctx(tmp_path, queue, jq))
    assert not (bundle / "SCORED").exists() and not (bundle / "FAILED").exists()


def test_a_row_count_that_differs_from_the_manifest_fails_the_bundle(
    tmp_path, queue, jq, judge
):
    bundle = make_bundle(queue)
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["row_count"] = 99
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "FAILED"
    assert "99" in (bundle / "FAILED").read_text()


# --- 3.5 offline equals inline --------------------------------------------------


def test_offline_aggregates_equal_inline_aggregates(tmp_path, queue, jq, judge):
    rows, keys = make_rows(23, drafts=(3, 12))
    settings = ragas_settings()
    inline_by_key = {key: {} for key in keys}
    outcome = RagasScoreOutcome()
    score_ragas(
        rows,
        keys,
        inline_by_key,
        enabled_metrics=settings["enabled_metrics"],
        ragas_settings=settings,
        verbosity=0,
        judge_factory=lambda: types.SimpleNamespace(),
        embeddings_factory=OpenAIEmbeddings,
        judge_provider="vllm",
        judge_model=MODEL,
        outcome=outcome,
    )

    bundle = make_bundle(queue, count=23, drafts=(3, 12), settings=settings)
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "SCORED"
    offline = judge_scores(bundle)

    for name, value in outcome.aggregates.items():
        if isinstance(value, float) and math.isnan(value):
            assert offline["aggregates"][name] is None
        else:
            assert offline["aggregates"][name] == pytest.approx(value)
    for key, cells in inline_by_key.items():
        for metric, value in cells.items():
            recorded = offline["scores"][key][metric]
            if math.isnan(value):
                assert recorded is None
            else:
                assert recorded == pytest.approx(value)
    assert offline["nan_counts"] == {"answer_relevancy": 2, "context_recall": 2}


# --- 3.6a identity and settings --------------------------------------------------


def test_the_scorer_passes_the_manifest_settings_to_ragas(tmp_path, queue, jq, judge):
    settings = ragas_settings(timeout=600, max_workers=6, batch_size=4)
    bundle = make_bundle(queue, settings=settings)
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "SCORED"
    for call in judge.calls:
        assert call["kwargs"]["batch_size"] == 4
        assert call["kwargs"]["run_config"].timeout == 600
        assert call["kwargs"]["run_config"].max_workers == 6
    identity = judge_scores(bundle)["ragas_judge_identity_effective"]
    assert identity["timeout"] == 600 and identity["max_workers"] == 6
    assert identity["batch_size"] == 4 and identity["chunk_size"] == 8


def test_the_offline_identity_has_the_inline_shape(tmp_path, queue, jq, judge):
    bundle = make_bundle(queue)
    score_bundle(bundle, make_ctx(tmp_path, queue, jq))
    identity = judge_scores(bundle)["ragas_judge_identity_effective"]
    assert set(identity) == {"provider", "model", "base_url", "mode", *IDENTITY_FIELDS}
    assert identity["model"] == MODEL and identity["model_revision"] == REVISION
    assert identity["precision"] == "bf16"
    assert identity["scorer"] == {
        "code_digest": CODE,
        "package_digest": PACKAGE,
        "image_digest": "sha256:scorer-image",
        "ragas_version": "0.3.5",
    }


def test_two_jobs_with_the_same_settings_differ_only_in_execution(
    tmp_path, queue, jq, judge
):
    one = make_bundle(queue, "a-20261023_120000")
    two = make_bundle(queue, "b-20261023_120000")
    score_bundle(one, make_ctx(tmp_path, queue, jq, job_id="1001"))
    score_bundle(two, make_ctx(tmp_path, queue, jq, job_id="2002"))
    first, second = judge_scores(one), judge_scores(two)
    assert (
        first["ragas_judge_identity_effective"]
        == second["ragas_judge_identity_effective"]
    )
    assert first["judge_execution"]["job_id"] == "1001"
    assert second["judge_execution"]["job_id"] == "2002"
    assert first["judge_execution"]["node"] == "holygpu8a12345"
    assert "judge_identity" not in first


def test_the_judge_scores_record_the_effective_settings(tmp_path, queue, jq, judge):
    bundle = make_bundle(queue)
    score_bundle(bundle, make_ctx(tmp_path, queue, jq))
    record = judge_scores(bundle)
    assert record["ragas_effective_settings"] == {"timeout": 600, "max_workers": 6}
    assert record["rows_sha256"].startswith("sha256:")


# --- 3.1b scorer identity -------------------------------------------------------


def test_a_package_mismatch_writes_nothing_and_status_names_both(
    tmp_path, queue, jq, judge
):
    bundle = make_bundle(queue, package="sha256:otherpkg")
    built = []
    assert run_job(make_ctx(tmp_path, queue, jq, judge_built=built)) == 0
    assert built == []
    assert sorted(p.name for p in bundle.iterdir()) == [
        "BUNDLE_COMPLETE",
        "READY",
        "manifest.json",
        "rows.jsonl",
    ]
    ctx = make_ctx(tmp_path, queue, jq)
    status = "\n".join(jq.status_lines(queue, ctx.judge_env, ctx.sidecar))
    assert "sha256:otherpkg" in status and PACKAGE in status

    matching = write_sidecar(tmp_path, package="sha256:otherpkg", name="new.json")
    later = make_ctx(
        tmp_path,
        queue,
        jq,
        sidecar_path=matching,
        live={"code_digest": CODE, "package_digest": "sha256:otherpkg"},
    )
    assert run_job(later) == 0
    assert (bundle / "SCORED").exists() and (bundle / "claim.1").exists()


def test_a_stale_sidecar_claims_nothing_and_loads_no_model(tmp_path, queue, jq, judge):
    bundle = make_bundle(queue)
    built = []
    ctx = make_ctx(
        tmp_path,
        queue,
        jq,
        live={"code_digest": CODE, "package_digest": "sha256:live"},
        judge_built=built,
    )
    assert preflight(ctx) == "stale"
    assert run_job(ctx) == 2
    record = queue / f"SCORER_STALE.{ctx.sidecar.token}"
    assert PACKAGE in record.read_text() and "sha256:live" in record.read_text()
    assert built == [] and judge.calls == []
    assert not list(bundle.glob("claim.*"))
    assert jq.available(queue, ctx.judge_env, ctx.sidecar) == []

    rebuilt = write_sidecar(tmp_path, package="sha256:live", name="rebuilt.json")
    fresh = jq.read_sidecar(rebuilt)
    assert fresh.token != ctx.sidecar.token
    assert "SCORER_STALE" not in (
        jq.match_reason(bundle, ctx.judge_env, fresh, queue) or ""
    )


def test_a_live_mismatch_after_a_claim_marks_the_bundle_incompatible(
    tmp_path, queue, jq, judge
):
    own = make_bundle(queue, "a-20261023_120000", package="sha256:old")
    (own / "claim.1").write_text("1001\n")
    other = make_bundle(queue, "b-20261023_120000")
    ctx = make_ctx(tmp_path, queue, jq)
    assert run_job(ctx) == 0
    record = own / f"INCOMPATIBLE.{ctx.sidecar.token}"
    assert "sha256:old" in record.read_text() and PACKAGE in record.read_text()
    assert not (own / "SCORED").exists() and not (own / "FAILED").exists()
    assert not list(own.glob("scores.*"))
    assert (other / "SCORED").exists()
    # Excluded for this sidecar (test_judge_queue covers the INCOMPATIBLE rule).
    assert jq.match_reason(own, ctx.judge_env, ctx.sidecar, queue) is not None


# --- 3.2 the time budget in the job ----------------------------------------------


def test_a_new_job_claims_only_the_first_bundle_at_start(
    tmp_path, queue, jq, monkeypatch
):
    bundles = [
        make_bundle(queue, f"{name}-20261023_120000", count=109, drafts=())
        for name in "abc"
    ]
    for age, bundle in enumerate(bundles):
        os.utime(bundle / "READY", (1000 + age, 1000 + age))
    claimed_at_first_call = []

    def on_call():
        if not claimed_at_first_call:
            claimed_at_first_call.append(
                [b.name for b in bundles if list(b.glob("claim.*"))]
            )

    stub = StubJudge(on_call=on_call)
    _install_ragas_stub(monkeypatch, stub.evaluate)
    monkeypatch.setattr(jq, "time_left", lambda job_id: 60 * 60)
    assert run_job(make_ctx(tmp_path, queue, jq)) == 0
    assert claimed_at_first_call == [["a-20261023_120000-arm0"]]
    assert all((b / "SCORED").exists() for b in bundles)


def test_a_bundle_that_needs_more_time_than_is_left_stays_ready(
    tmp_path, queue, jq, judge, monkeypatch
):
    bundles = [
        make_bundle(queue, f"{name}-20261023_120000", count=109, drafts=())
        for name in "ab"
    ]
    ticks = iter([0.0, 3600.0])
    monkeypatch.setattr(jq, "time_left", lambda job_id: 10 * 60)
    ctx = make_ctx(tmp_path, queue, jq, monotonic=lambda: next(ticks))
    assert run_job(ctx) == 0
    scored = [b for b in bundles if (b / "SCORED").exists()]
    assert len(scored) == 1
    waiting = [b for b in bundles if b not in scored][0]
    assert not list(waiting.glob("claim.*"))


def test_a_requeued_job_resumes_its_claim_with_no_other_work(
    tmp_path, queue, jq, judge
):
    bundle = make_bundle(queue)
    (bundle / "claim.1").write_text("1001\n")
    ctx = make_ctx(tmp_path, queue, jq)
    assert preflight(ctx) == "work"
    assert run_job(ctx) == 0
    assert (bundle / "SCORED").exists()
    assert sorted(p.name for p in bundle.glob("claim.*")) == ["claim.1"]
    assert judge_scores(bundle)["judge_execution"]["claimed_bundles"] == [bundle.name]


def test_with_no_work_the_job_exits_before_it_builds_a_judge(
    tmp_path, queue, jq, judge
):
    built = []
    ctx = make_ctx(tmp_path, queue, jq, judge_built=built)
    assert preflight(ctx) == "none"
    assert run_job(ctx) == 0
    assert built == []


def test_a_requeued_claim_for_another_judge_is_not_scored(tmp_path, queue, jq, judge):
    """judge.env changed before the requeue: the job's own old claim asks for
    another revision, so the job neither scores it nor counts it as work."""
    settings = ragas_settings(evaluator_model_revision="rev-old")
    bundle = make_bundle(queue, settings=settings)
    (bundle / "claim.1").write_text("1001\n")
    built = []
    ctx = make_ctx(tmp_path, queue, jq, judge_built=built)
    assert preflight(ctx) == "none"
    assert run_job(ctx) == 0
    assert built == [] and judge.calls == []
    assert score_bundle(bundle, ctx) == "OTHER_JUDGE"
    assert not any((bundle / m).exists() for m in ("SCORED", "FAILED"))
    assert not list(bundle.glob("scores.*")) and not list(bundle.glob("INCOMPATIBLE.*"))


def test_a_finalized_scores_file_does_not_raise_the_measured_rate(
    tmp_path, queue, jq, judge, monkeypatch
):
    """Finalizing a dead job's complete judge_scores.json judges no row, so it
    must not make the next claim round think the job is fast."""
    done = make_bundle(queue, "a-20261023_120000", count=12)
    assert score_bundle(done, make_ctx(tmp_path, queue, jq)) == "SCORED"
    (done / "SCORED").unlink()
    (done / "claim.1").write_text("1001\n")
    waiting = [
        make_bundle(queue, f"{name}-20261023_120000", count=109, drafts=())
        for name in "bc"
    ]
    calls = len(judge.calls)
    # 50 minutes at the bootstrap 2 rows per minute: a 109-row bundle needs
    # 54.5 minutes, so only an inflated rate would claim one.
    monkeypatch.setattr(jq, "time_left", lambda job_id: 50 * 60)
    assert run_job(make_ctx(tmp_path, queue, jq)) == 0
    assert (done / "SCORED").exists()
    assert len(judge.calls) == calls
    assert not any(list(b.glob("claim.*")) for b in waiting)


def test_a_resumed_bundle_counts_only_the_pairs_judged_now(
    tmp_path, queue, jq, monkeypatch
):
    settings = ragas_settings(enabled_metrics=["answer_relevancy"])
    bundle = make_bundle(queue, count=40, drafts=(), settings=settings)
    first = StubJudge(fail_after=4)
    _install_ragas_stub(monkeypatch, first.evaluate)
    with pytest.raises(RuntimeError):
        score_bundle(bundle, make_ctx(tmp_path, queue, jq, chunk_size=8))
    second = StubJudge()
    _install_ragas_stub(monkeypatch, second.evaluate)
    ctx = make_ctx(tmp_path, queue, jq, chunk_size=8)
    assert score_bundle(bundle, ctx) == "SCORED"
    assert ctx.pairs_judged == 8


def test_the_budget_counts_metric_pairs_once_a_rate_is_measured(
    tmp_path, queue, jq, judge, monkeypatch
):
    """A rate measured on a one-metric bundle must not price a five-metric
    bundle as if each row cost one judge call."""
    one_metric = ragas_settings(enabled_metrics=["answer_relevancy"])
    measured = make_bundle(
        queue, "a-20261023_120000", count=40, drafts=(), settings=one_metric
    )
    (measured / "claim.1").write_text("1001\n")
    five_metrics = ragas_settings(
        enabled_metrics=[
            "answer_relevancy",
            "faithfulness",
            "context_precision",
            "context_recall",
            "answer_correctness",
        ]
    )
    waiting = make_bundle(
        queue, "b-20261023_120000", count=40, drafts=(), settings=five_metrics
    )
    ticks = iter([0.0, 60.0])
    # 40 pairs in one minute: the five-metric bundle (200 pairs) needs five
    # minutes, and four are left. Counted in rows it would need one.
    monkeypatch.setattr(jq, "time_left", lambda job_id: 4 * 60)
    ctx = make_ctx(tmp_path, queue, jq, monotonic=lambda: next(ticks))
    assert run_job(ctx) == 0
    assert (measured / "SCORED").exists()
    assert not list(waiting.glob("claim.*"))


def test_a_job_that_lost_its_claim_writes_nothing(tmp_path, queue, jq, judge):
    bundle = make_bundle(queue)
    (bundle / "claim.1").write_text("1001\n")
    (bundle / "claim.2").write_text("2002\n")
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "LOST_CLAIM"
    assert judge.calls == []
    assert sorted(p.name for p in bundle.iterdir()) == [
        "BUNDLE_COMPLETE",
        "READY",
        "claim.1",
        "claim.2",
        "manifest.json",
        "rows.jsonl",
    ]


def test_a_takeover_during_scoring_stops_the_old_job_before_its_next_write(
    tmp_path, queue, jq, monkeypatch
):
    settings = ragas_settings(enabled_metrics=["answer_relevancy"])
    bundle = make_bundle(queue, count=24, drafts=(), settings=settings)
    (bundle / "claim.1").write_text("1001\n")

    def take_over():
        if not (bundle / "claim.2").exists():
            (bundle / "claim.2").write_text("2002\n")

    stub = StubJudge(on_call=take_over)
    _install_ragas_stub(monkeypatch, stub.evaluate)
    ctx = make_ctx(tmp_path, queue, jq, chunk_size=8)
    assert score_bundle(bundle, ctx) == "LOST_CLAIM"
    assert len(stub.calls) == 1
    assert not list(bundle.glob("scores.*"))
    assert not (bundle / "judge_scores.json").exists()
    assert not (bundle / "SCORED").exists()


def test_a_job_that_lost_its_claim_does_not_finalize_a_scores_file(
    tmp_path, queue, jq, judge
):
    bundle = make_bundle(queue)
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "SCORED"
    (bundle / "SCORED").unlink()
    (bundle / "claim.1").write_text("2002\n")
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "LOST_CLAIM"
    assert not (bundle / "SCORED").exists()


def test_a_scored_bundle_is_not_scored_again(tmp_path, queue, jq, judge):
    bundle = make_bundle(queue)
    ctx = make_ctx(tmp_path, queue, jq)
    assert score_bundle(bundle, ctx) == "SCORED"
    calls = len(judge.calls)
    assert score_bundle(bundle, ctx) == "SCORED"
    assert len(judge.calls) == calls


def test_a_valid_scores_file_left_by_a_dead_job_is_finalized_without_scoring(
    tmp_path, queue, jq, judge
):
    """A bf16 job wrote judge_scores.json and died before SCORED. The fp8 job that
    takes over keeps that complete, self-labelled result: it writes SCORED and
    calls no judge."""
    bundle = make_bundle(queue)
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "SCORED"
    (bundle / "SCORED").unlink()
    before = (bundle / "judge_scores.json").read_bytes()
    calls = len(judge.calls)
    fp8 = make_ctx(tmp_path, queue, jq, env={"JUDGE_PRECISION": "fp8"})
    assert score_bundle(bundle, fp8) == "SCORED"
    assert len(judge.calls) == calls
    assert (bundle / "judge_scores.json").read_bytes() == before
    assert judge_scores(bundle)["ragas_judge_identity_effective"]["precision"] == "bf16"


def _scored_then_edited(tmp_path, queue, jq, edit):
    bundle = make_bundle(queue)
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "SCORED"
    (bundle / "SCORED").unlink()
    record = judge_scores(bundle)
    (bundle / "judge_scores.json").write_text(json.dumps(edit(record)))
    return bundle


@pytest.mark.parametrize(
    "edit,reason",
    [
        (lambda record: {"kept": True}, "lacks"),
        (lambda record: dict(record, rows_sha256="sha256:other"), "sha256:other"),
        (
            lambda record: dict(
                record,
                scores={k: v for k, v in record["scores"].items() if k != "question_2"},
            ),
            "question_2",
        ),
    ],
)
def test_an_invalid_scores_file_makes_the_bundle_failed(
    tmp_path, queue, jq, judge, edit, reason
):
    bundle = _scored_then_edited(tmp_path, queue, jq, edit)
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "FAILED"
    failed = (bundle / "FAILED").read_text()
    assert "judge_scores.json" in failed and reason in failed
    assert not (bundle / "SCORED").exists()


def test_a_scores_file_that_does_not_load_makes_the_bundle_failed(
    tmp_path, queue, jq, judge
):
    bundle = make_bundle(queue)
    (bundle / "judge_scores.json").write_text("{")
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "FAILED"
    assert "judge_scores.json does not load" in (bundle / "FAILED").read_text()


# --- an arm the judge never scored (#518) ---------------------------------------


def test_a_bundle_with_no_eligible_pair_records_no_judge(tmp_path, queue, jq, judge):
    """Inline, an arm whose metrics have no eligible row never calls the judge
    and records null settings and identity (#518); offline does the same."""
    settings = ragas_settings(enabled_metrics=["context_recall"])
    bundle = make_bundle(queue, count=4, drafts=(1, 2, 3, 4), settings=settings)
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "SCORED"
    record = judge_scores(bundle)
    assert judge.calls == []
    assert record["ragas_effective_settings"] is None
    assert record["ragas_judge_identity_effective"] is None
    assert record["aggregates"] == {
        "aggregate_context_recall": None,
        "context_recall_scored": "0 of 4",
    }


def test_a_bundle_with_no_row_gets_the_inline_all_failed_aggregates(
    tmp_path, queue, jq, judge
):
    from src.utils.benchmark_resilience import build_ragas_aggregates
    from src.utils.benchmark_schema import json_safe

    bundle = make_bundle(queue, count=0, drafts=())
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "SCORED"
    record = judge_scores(bundle)
    assert record["ragas_effective_settings"] is None
    assert record["ragas_judge_identity_effective"] is None
    assert record["aggregates"] == json_safe(
        build_ragas_aggregates(None, enabled_metrics=METRICS)
    )


@pytest.mark.parametrize("judged", [True, False])
def test_the_judge_fields_must_match_whether_the_judge_scored(
    tmp_path, queue, jq, judge, judged
):
    if judged:
        bundle = make_bundle(queue)
    else:
        settings = ragas_settings(enabled_metrics=["context_recall"])
        bundle = make_bundle(queue, count=2, drafts=(1, 2), settings=settings)
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "SCORED"
    (bundle / "SCORED").unlink()
    record = judge_scores(bundle)
    record["ragas_judge_identity_effective"] = None if judged else {"provider": "x"}
    (bundle / "judge_scores.json").write_text(json.dumps(record))
    assert score_bundle(bundle, make_ctx(tmp_path, queue, jq)) == "FAILED"
    assert "ragas_judge_identity_effective" in (bundle / "FAILED").read_text()
