"""The trial scorer's ragas concurrency override (--max-workers): the bundle manifest's
value is the default, an override reaches ragas and is recorded as the effective setting
(fasrc/archi#737, step 5.3 follow-up: the H200 judge ran at 6 workers, the card idle)."""

from __future__ import annotations

import json

import pytest

from scripts.benchmarking.judge_trial import trial_score as ts
from tests.unit.test_judge_trial_score import LIVE, _bundle, _env_file, _StubBuilder


def _score(tmp_path, **kwargs):
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
        build_scorer=builder,
        live_identity=LIVE,
        **kwargs,
    )
    return builder, json.loads(target.read_text())


def test_the_manifest_concurrency_is_the_default(tmp_path):
    builder, doc = _score(tmp_path)
    assert builder.kwargs["ragas_settings"]["max_workers"] == 6
    assert doc["ragas_effective_settings"] == {
        "timeout": 600,
        "max_workers": 6,
        "max_workers_source": "manifest",
    }
    assert doc["ragas_judge_identity_effective"]["max_workers"] == 6


def test_an_override_reaches_ragas_and_the_effective_settings(tmp_path):
    builder, doc = _score(tmp_path, max_workers=32)
    assert builder.kwargs["ragas_settings"] == {
        "timeout": 600,
        "max_workers": 32,
        "batch_size": None,
    }
    assert doc["ragas_effective_settings"] == {
        "timeout": 600,
        "max_workers": 32,
        "max_workers_source": "override",
    }
    assert doc["ragas_judge_identity_effective"]["max_workers"] == 32


def test_a_non_positive_override_is_refused(tmp_path):
    with pytest.raises(ts.Refused, match="max-workers"):
        _score(tmp_path, max_workers=0)


def test_main_passes_the_override_and_refuses_zero(tmp_path, capsys):
    bundle = _bundle(tmp_path)
    out = tmp_path / "scores"
    out.mkdir()
    env = _env_file(tmp_path)
    args = ["--bundle", str(bundle), "--out-dir", str(out), "--judge-env", str(env)]
    assert ts.main([*args, "--socket", "/s", "--max-workers", "0"]) == 2
    assert "max-workers" in capsys.readouterr().err
