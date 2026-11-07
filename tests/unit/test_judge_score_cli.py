"""The scorer CLI ``scripts/benchmarking/judge/score.py`` (#732, task 3.6).

``write-identity`` writes the scorer sidecar at image build (#733 calls it),
``check`` is the sbatch script's go or no-go before vLLM starts, and ``run``
scores the job's bundles through the Unix-socket judge.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from src.utils.benchmark_provenance import collect_code_version
from tests.unit.test_judge_scorer import (
    CODE,
    MODEL,
    PACKAGE,
    StubJudge,
    make_bundle,
    write_judge_env,
    write_sidecar,
)
from tests.unit.test_ragas_scoring_characterization import _install_ragas_stub

SCORE_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "benchmarking"
    / "judge"
    / "score.py"
)


@pytest.fixture
def score(monkeypatch):
    spec = importlib.util.spec_from_file_location("judge_score_cli", SCORE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "live_identity",
        lambda package_dir: {"code_digest": CODE, "package_digest": PACKAGE},
    )
    return module


@pytest.fixture
def files(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    return {
        "queue": queue,
        "env": write_judge_env(tmp_path),
        "sidecar": write_sidecar(tmp_path),
    }


def _args(files, command, *extra):
    return [
        command,
        "--queue",
        str(files["queue"]),
        "--judge-env",
        str(files["env"]),
        "--sidecar",
        str(files["sidecar"]),
        "--job-id",
        "1001",
        *extra,
    ]


def test_write_identity_writes_the_sidecar(tmp_path, score, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text("x = 1\n")
    monkeypatch.setattr(
        "src.utils.judge_identity.collect_package_digest",
        lambda: {"digest": "sha256:pkg", "source": "pip freeze"},
    )
    out = tmp_path / "scorer-identity.json"
    argv = ["write-identity", "--out", str(out), "--image-digest", "sha256:img"]
    assert score.main(argv + ["--package-dir", str(src)]) == 0
    identity = json.loads(out.read_text())
    assert identity["code_digest"] == collect_code_version(str(src), None)["digest"]
    assert identity["package_digest"] == "sha256:pkg"
    assert identity["image_digest"] == "sha256:img"


def test_write_identity_refuses_when_a_digest_is_unavailable(tmp_path, score, capsys):
    out = tmp_path / "scorer-identity.json"
    argv = ["write-identity", "--out", str(out), "--package-dir", str(tmp_path / "x")]
    assert score.main(argv) == 1
    assert "code digest" in capsys.readouterr().err
    assert not out.exists()


def test_check_reports_work_none_and_a_stale_sidecar(files, score, monkeypatch):
    assert score.main(_args(files, "check")) == 3
    make_bundle(files["queue"])
    assert score.main(_args(files, "check")) == 0
    monkeypatch.setattr(
        score,
        "live_identity",
        lambda package_dir: {"code_digest": CODE, "package_digest": "sha256:live"},
    )
    assert score.main(_args(files, "check")) == 2
    assert list(files["queue"].glob("SCORER_STALE.*"))


def test_check_compares_the_image_digest_the_job_passes(files, score):
    make_bundle(files["queue"])
    assert (
        score.main(_args(files, "check", "--image-digest", "sha256:scorer-image")) == 0
    )
    assert not list(files["queue"].glob("SCORER_STALE.*"))
    assert score.main(_args(files, "check", "--image-digest", "sha256:other")) == 2
    (record,) = files["queue"].glob("SCORER_STALE.*")
    assert "sha256:other" in record.read_text()


def _run_args(files, *extra):
    return _args(files, "run", "--socket", "/nonexistent/vllm.sock", *extra)


def test_run_records_the_vllm_image_digest_the_job_measured(files, score, monkeypatch):
    stub = StubJudge()
    _install_ragas_stub(monkeypatch, stub.evaluate)
    monkeypatch.setenv("VLLM_API_KEY", "k-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    bundle = make_bundle(files["queue"])
    assert score.main(_run_args(files, "--server-image-digest", "sha256:vllm")) == 0
    record = json.loads((bundle / "judge_scores.json").read_text())
    identity = record["ragas_judge_identity_effective"]
    assert identity["server_image_digest"] == "sha256:vllm"


def test_run_refuses_a_judge_env_digest_of_another_vllm_image(files, score, capsys):
    files["env"].write_text(
        files["env"].read_text() + "JUDGE_IMAGE_DIGEST=sha256:configured\n"
    )
    make_bundle(files["queue"])
    assert score.main(_run_args(files, "--server-image-digest", "sha256:vllm")) == 1
    err = capsys.readouterr().err
    assert "sha256:configured" in err and "sha256:vllm" in err


def test_check_and_run_need_a_job_id(files, score, monkeypatch, capsys):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    argv = _args(files, "check")
    argv = argv[: argv.index("--job-id")]
    assert score.main(argv) == 1
    assert "SLURM_JOB_ID" in capsys.readouterr().err


def test_a_bad_judge_env_is_refused(files, score, capsys):
    files["env"].write_text("JUDGE_MODEL_ID=m\n")
    assert score.main(_args(files, "check")) == 1
    assert "JUDGE_MODEL_REVISION" in capsys.readouterr().err


def test_run_scores_through_the_socket_judge(files, score, monkeypatch):
    stub = StubJudge()
    _install_ragas_stub(monkeypatch, stub.evaluate)
    monkeypatch.setenv("VLLM_API_KEY", "k-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("SLURM_RESTART_COUNT", "1")
    bundle = make_bundle(files["queue"])
    argv = _args(
        files, "run", "--socket", "/nonexistent/vllm.sock", "--chunk-size", "4"
    )
    assert score.main(argv) == 0
    assert (bundle / "SCORED").exists()
    record = json.loads((bundle / "judge_scores.json").read_text())
    identity = record["ragas_judge_identity_effective"]
    assert identity["model"] == MODEL and identity["chunk_size"] == 4
    assert identity["embedding"]["class"] == "OpenAIEmbeddings"
    assert record["judge_execution"]["requeue_count"] == 1
    assert all(len(call["users"]) <= 4 for call in stub.calls)


def test_run_stops_on_a_stale_sidecar(files, score, monkeypatch):
    make_bundle(files["queue"])
    monkeypatch.setattr(
        score,
        "live_identity",
        lambda package_dir: {"code_digest": "sha256:live", "package_digest": PACKAGE},
    )
    argv = _args(files, "run", "--socket", "/nonexistent/vllm.sock")
    assert score.main(argv) == 2
