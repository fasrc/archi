"""The judge-trial scorer: judge file rules, request options, checkpointed scoring and the
output document (fasrc/archi#737, step 5.3). No ragas, no server: the scorer builder is
a stub."""

from __future__ import annotations

import json

import pytest

from scripts.benchmarking.judge_trial import trial_score as ts
from src.utils.judge_bundle import write_judge_bundle
from src.utils.llm_usage import UsageRecorder
from src.utils.ragas_scoring import RagasScorer


class _Recorder(UsageRecorder):
    """A usage recorder whose snapshot is the stub's call count."""

    def __init__(self, calls):
        super().__init__("stub", "stub")
        self._calls = calls

    def snapshot(self):
        return {"calls": self._calls()}


ENV_TEXT = (
    "# a comment on its own line\n"
    "JUDGE_MODEL_ID=openai/gpt-oss-120b\n"
    "JUDGE_MODEL_REVISION=b5c939de\n"
    "JUDGE_PRECISION=checkpoint\n"
    "JUDGE_SERVER_ARGS=--max-model-len 65536 --limit-mm-per-prompt '{\"image\":0}'\n"
    "JUDGE_REASONING_EFFORT=medium\n"
    'JUDGE_CHAT_TEMPLATE_KWARGS={"enable_thinking": true}\n'
    "JUDGE_MAX_TOKENS=8192\n"
    "JUDGE_SEED=20261027\n"
)


def _env_file(tmp_path, text=ENV_TEXT):
    path = tmp_path / "judge.env"
    path.write_text(text)
    return path


def _rows(n):
    return (
        [
            {
                "user_input": f"q{i}",
                "retrieved_contexts": [f"c{i}"],
                "response": f"a{i}",
                "reference": f"r{i}",
            }
            for i in range(1, n + 1)
        ],
        [f"question_{i}" for i in range(1, n + 1)],
    )


def _bundle(tmp_path, n=3, metrics=("faithfulness", "answer_relevancy")):
    rows, keys = _rows(n)
    write_judge_bundle(
        tmp_path / "bundles",
        result_basename="run-20261012_000000",
        arm_index=0,
        arm_count=1,
        rows=rows,
        keys=keys,
        enabled_metrics=list(metrics),
        ragas_settings={
            "timeout": 600,
            "max_workers": 6,
            "batch_size": False,
            "embedding_model": "HuggingFace",
            "evaluator_model": "sonnet",
        },
        verbosity=0,
        code_version={"digest": "sha256:" + "a" * 64},
        package_digest="x",
        judge_mode="inline+bundle",
    )
    return tmp_path / "bundles" / "run-20261012_000000-arm0"


class _StubBuilder:
    """Stands in for build_ragas_scorer: deterministic scores, optional failure."""

    def __init__(self, fail_on_call=None, value=0.5):
        self.calls = 0
        self.fail_on_call = fail_on_call
        self.value = value
        self.built = 0

    def __call__(self, **kwargs):
        self.built += 1
        self.kwargs = kwargs

        def score_fn(metric, rows):
            self.calls += 1
            if self.fail_on_call is not None and self.calls == self.fail_on_call:
                raise RuntimeError("the judge server stopped")
            return [
                self.value if metric == "faithfulness" else float("nan") for _ in rows
            ]

        return RagasScorer(
            metrics=list(kwargs["enabled_metrics"]),
            score_fn=score_fn,
            recorder=_Recorder(lambda: self.calls),
            decoding={"temperature": 0.0},
            embedding={"provider": "HuggingFaceEmbeddings"},
        )


LIVE = {"code_digest": "sha256:" + "c" * 64, "package_digest": "sha256:" + "d" * 64}


# --- the judge file --------------------------------------------------------------------------


def test_read_judge_env_follows_the_production_rules(tmp_path):
    env = ts.read_judge_env(_env_file(tmp_path))
    assert env["JUDGE_MODEL_ID"] == "openai/gpt-oss-120b"
    assert (
        env["JUDGE_SERVER_ARGS"]
        == "--max-model-len 65536 --limit-mm-per-prompt '{\"image\":0}'"
    )
    assert env["JUDGE_CHAT_TEMPLATE_KWARGS"] == '{"enable_thinking": true}'


@pytest.mark.parametrize(
    "missing", ["JUDGE_MODEL_ID", "JUDGE_MODEL_REVISION", "JUDGE_PRECISION"]
)
def test_read_judge_env_refuses_a_missing_model_key(tmp_path, missing):
    text = "\n".join(l for l in ENV_TEXT.splitlines() if not l.startswith(missing))
    with pytest.raises(ts.Refused, match=missing):
        ts.read_judge_env(_env_file(tmp_path, text))


def test_read_judge_env_refuses_a_line_without_equals(tmp_path):
    with pytest.raises(ts.Refused, match="line 2"):
        ts.read_judge_env(_env_file(tmp_path, "JUDGE_MODEL_ID=x\nnot a pair\n"))


def test_request_options_carry_effort_template_kwargs_tokens_and_seed(tmp_path):
    env = ts.read_judge_env(_env_file(tmp_path))
    assert ts.request_options(env) == {
        "reasoning_effort": "medium",
        "extra_body": {"chat_template_kwargs": {"enable_thinking": True}},
        "max_tokens": 8192,
        "seed": 20261027,
    }


def test_request_options_refuse_bad_json_and_bad_integers(tmp_path):
    env = ts.read_judge_env(_env_file(tmp_path))
    with pytest.raises(ts.Refused, match="JSON"):
        ts.request_options({**env, "JUDGE_CHAT_TEMPLATE_KWARGS": "{nope"})
    with pytest.raises(ts.Refused, match="JUDGE_MAX_TOKENS"):
        ts.request_options({**env, "JUDGE_MAX_TOKENS": "many"})


def test_judge_factory_needs_exactly_one_transport(tmp_path):
    env = ts.read_judge_env(_env_file(tmp_path))
    with pytest.raises(ts.Refused):
        ts.judge_factory_for(env)
    with pytest.raises(ts.Refused):
        ts.judge_factory_for(env, socket_path="/s", base_url="http://x")


def test_pick_metrics_manifest_all_and_list():
    manifest = {"enabled_metrics": ["faithfulness"]}
    assert ts.pick_metrics(manifest, None) == ["faithfulness"]
    assert ts.pick_metrics(manifest, "all") == list(ts.RAGAS_METRIC_NAMES)
    assert ts.pick_metrics(manifest, "context_recall,faithfulness") == [
        m for m in ts.RAGAS_METRIC_NAMES if m in ("faithfulness", "context_recall")
    ]
    with pytest.raises(ts.Refused, match="unknown"):
        ts.pick_metrics(manifest, "faithfulnes")


# --- scoring -------------------------------------------------------------------------------


def test_scores_every_pair_and_writes_the_document(tmp_path):
    bundle = _bundle(tmp_path)
    env = ts.read_judge_env(_env_file(tmp_path))
    out = tmp_path / "scores"
    out.mkdir()
    builder = _StubBuilder()

    target = ts.score_trial_bundle(
        bundle,
        out,
        env,
        judge_factory=lambda: None,
        embeddings_factory=lambda: None,
        chunk_size=2,
        server_image_digest="sha256:vllm",
        scorer_image_digest="sha256:scorer",
        job_id="4242",
        build_scorer=builder,
        live_identity=LIVE,
    )

    assert target == out / "run-20261012_000000-arm0.judge_scores.json"
    doc = json.loads(target.read_text())
    assert doc["row_count"] == 3 and doc["metrics"] == [
        "answer_relevancy",
        "faithfulness",
    ]
    assert doc["bundle_judge_mode"] == "inline+bundle"
    assert set(doc["scores"]) == {"question_1", "question_2", "question_3"}
    assert doc["scores"]["question_1"]["faithfulness"] == 0.5
    assert doc["nan_counts"] == {"answer_relevancy": 3, "faithfulness": 0}
    assert doc["errors"]["answer_relevancy"] == ["the judge returned no score"]
    assert doc["aggregates"]["aggregate_faithfulness"] == 0.5
    identity = doc["ragas_judge_identity_effective"]
    assert identity["model"] == "openai/gpt-oss-120b"
    assert identity["model_revision"] == "b5c939de"
    assert identity["precision"] == "checkpoint"
    assert identity["server_image_digest"] == "sha256:vllm"
    assert identity["scorer"]["image_digest"] == "sha256:scorer"
    assert identity["scorer"]["code_digest"] == LIVE["code_digest"]
    assert identity["decoding"]["request_options"]["reasoning_effort"] == "medium"
    assert identity["chunk_size"] == 2 and identity["timeout"] == 600
    ex = doc["judge_execution"]
    assert ex["job_id"] == "4242" and ex["pairs_total"] == 6
    assert ex["pairs_resumed_from_checkpoint"] == 0 and ex["pairs_judged_this_run"] == 6
    assert len(ex["chunk_seconds"]) == 4  # 3 rows in chunks of 2, per metric
    assert doc["judge_usage"] == {"calls": 4}
    # The ragas call settings come from the manifest, the metrics from the manifest.
    assert builder.kwargs["ragas_settings"] == {
        "timeout": 600,
        "max_workers": 6,
        "batch_size": None,
    }


def test_a_judge_failure_leaves_a_checkpoint_and_a_rerun_resumes(tmp_path):
    bundle = _bundle(tmp_path)
    env = ts.read_judge_env(_env_file(tmp_path))
    out = tmp_path / "scores"
    out.mkdir()

    with pytest.raises(RuntimeError, match="stopped"):
        ts.score_trial_bundle(
            bundle,
            out,
            env,
            judge_factory=lambda: None,
            embeddings_factory=lambda: None,
            chunk_size=2,
            build_scorer=_StubBuilder(fail_on_call=2),
            live_identity=LIVE,
        )
    partials = list(out.glob("run-20261012_000000-arm0.scores.*.partial.jsonl"))
    assert len(partials) == 1
    assert len(partials[0].read_text().splitlines()) == 2  # one chunk landed
    assert not (out / "run-20261012_000000-arm0.judge_scores.json").exists()

    builder = _StubBuilder()
    target = ts.score_trial_bundle(
        bundle,
        out,
        env,
        judge_factory=lambda: None,
        embeddings_factory=lambda: None,
        chunk_size=2,
        build_scorer=builder,
        live_identity=LIVE,
    )
    doc = json.loads(target.read_text())
    assert doc["judge_execution"]["pairs_resumed_from_checkpoint"] == 2
    assert doc["judge_execution"]["pairs_judged_this_run"] == 4
    assert (
        builder.calls == 3
    )  # 2 pairs done; 4 left = 1 chunk of answer_relevancy + 2 of faithfulness


def test_an_existing_scores_file_is_not_scored_again(tmp_path):
    bundle = _bundle(tmp_path)
    env = ts.read_judge_env(_env_file(tmp_path))
    out = tmp_path / "scores"
    out.mkdir()
    (out / "run-20261012_000000-arm0.judge_scores.json").write_text("{}")
    builder = _StubBuilder()
    ts.score_trial_bundle(
        bundle,
        out,
        env,
        judge_factory=lambda: None,
        embeddings_factory=lambda: None,
        build_scorer=builder,
        live_identity=LIVE,
    )
    assert builder.built == 0


def test_the_output_folder_must_be_outside_the_bundle(tmp_path):
    bundle = _bundle(tmp_path)
    env = ts.read_judge_env(_env_file(tmp_path))
    with pytest.raises(ts.Refused, match="outside"):
        ts.score_trial_bundle(
            bundle,
            bundle,
            env,
            judge_factory=lambda: None,
            embeddings_factory=lambda: None,
            build_scorer=_StubBuilder(),
            live_identity=LIVE,
        )


def test_main_refuses_a_bad_judge_file_before_any_server_call(tmp_path, capsys):
    bundle = _bundle(tmp_path)
    out = tmp_path / "scores"
    out.mkdir()
    bad = _env_file(tmp_path, "JUDGE_MODEL_ID=x\n")
    code = ts.main(
        [
            "--bundle",
            str(bundle),
            "--out-dir",
            str(out),
            "--judge-env",
            str(bad),
            "--socket",
            "/s",
        ]
    )
    assert code == 1
    assert "JUDGE_MODEL_REVISION" in capsys.readouterr().err
