"""Codex review of fasrc/archi#793 on trial_score.py: after a resume, the scores file must
account for every attempt (time and judge usage), not only the last one."""

from __future__ import annotations

import json

import pytest

from scripts.benchmarking.judge_trial import trial_score as ts
from tests.unit.test_judge_trial_score import LIVE, _bundle, _env_file, _StubBuilder


def _run(bundle, out, env, builder):
    return ts.score_trial_bundle(
        bundle,
        out,
        env,
        judge_factory=lambda: None,
        embeddings_factory=lambda: None,
        chunk_size=2,
        build_scorer=builder,
        live_identity=LIVE,
    )


def test_a_resumed_bundle_reports_cumulative_time_usage_and_every_attempt(tmp_path):
    bundle = _bundle(tmp_path)
    env = ts.read_judge_env(_env_file(tmp_path))
    out = tmp_path / "scores"
    out.mkdir()

    with pytest.raises(RuntimeError, match="stopped"):
        _run(bundle, out, env, _StubBuilder(fail_on_call=2))
    attempts = list(out.glob("run-20261012_000000-arm0.scores.*.attempts.jsonl"))
    assert len(attempts) == 1
    first = json.loads(attempts[0].read_text().splitlines()[0])
    assert first["outcome"] == "failed" and first["pairs_judged"] == 2
    assert first["judge_usage"] == {"calls": 2}

    builder = _StubBuilder()
    target = _run(bundle, out, env, builder)
    doc = json.loads(target.read_text())
    ex = doc["judge_execution"]
    assert [a["outcome"] for a in ex["attempts"]] == ["failed", "completed"]
    assert ex["attempts"][1]["pairs_judged"] == 4
    assert ex["pairs_resumed_from_checkpoint"] == 2 and ex["pairs_judged_this_run"] == 4
    # Cumulative over both attempts: 2 + 3 stub calls, and the sum of both elapsed times.
    assert doc["judge_usage"] == {"calls": 5}
    assert doc["judge_usage_this_attempt"] == {"calls": 3}
    assert ex["elapsed_seconds"] == pytest.approx(
        sum(a["elapsed_seconds"] for a in ex["attempts"]), abs=0.05
    )
    assert ex["elapsed_seconds"] >= ex["elapsed_seconds_this_attempt"]
    assert ex["pairs_per_minute"] is None or ex["pairs_per_minute"] > 0


def test_a_single_attempt_is_its_own_total(tmp_path):
    bundle = _bundle(tmp_path)
    env = ts.read_judge_env(_env_file(tmp_path))
    out = tmp_path / "scores"
    out.mkdir()
    doc = json.loads(_run(bundle, out, env, _StubBuilder()).read_text())
    ex = doc["judge_execution"]
    assert len(ex["attempts"]) == 1 and ex["attempts"][0]["outcome"] == "completed"
    assert doc["judge_usage"] == doc["judge_usage_this_attempt"] == {"calls": 4}
    assert ex["elapsed_seconds"] == ex["elapsed_seconds_this_attempt"]


def test_merge_usage_sums_numbers_and_merges_models():
    a = {
        "calls": 2,
        "input_tokens": 10,
        "by_model": [
            {"provider": "vllm", "model": "m", "calls": 2, "input_tokens": 10}
        ],
    }
    b = {
        "calls": 3,
        "input_tokens": 5,
        "output_tokens": 7,
        "by_model": [
            {"provider": "vllm", "model": "m", "calls": 3, "input_tokens": 5},
            {"provider": "vllm", "model": "n", "calls": 1},
        ],
    }
    merged = ts._merge_usage(a, b)
    assert merged["calls"] == 5 and merged["input_tokens"] == 15
    assert merged["output_tokens"] == 7
    assert sorted(merged["by_model"], key=lambda e: e["model"]) == [
        {"provider": "vllm", "model": "m", "calls": 5, "input_tokens": 15},
        {"provider": "vllm", "model": "n", "calls": 1},
    ]
