"""The judge-trial job script, dry run: which bundles a job scores and where, the bundle
partition for data-parallel jobs, and the scorer's concurrency knobs (fasrc/archi#737)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SBATCH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "benchmarking"
    / "judge_trial"
    / "judge_trial.sbatch"
)

JUDGE_ENV = (
    "JUDGE_MODEL_ID=org/judge\n"
    "JUDGE_MODEL_REVISION=rev1\n"
    "JUDGE_PRECISION=bf16\n"
    "JUDGE_BOOTSTRAP_ROWS_PER_MINUTE=20\n"
    "JUDGE_GPUS=1\n"
    "JUDGE_SERVER_ARGS=--max-model-len 4096\n"
)


def _package(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "scripts").mkdir(parents=True)
    shutil.copy(SBATCH, pkg / "scripts" / "judge_trial.sbatch")
    (pkg / "scripts" / "trial_score.py").write_text("# stand-in\n")
    (pkg / "judges").mkdir()
    (pkg / "judges" / "j.env").write_text(JUDGE_ENV)
    (pkg / "hf" / "hub").mkdir(parents=True)
    (pkg / "images").mkdir()
    for folder in (
        "bundles/r1-arm0",
        "bundles/r2-arm0",
        "bundles/control-x-arm0",
        "bundles-unscored/u1-arm0",
    ):
        (pkg / folder).mkdir(parents=True)
        (pkg / folder / "BUNDLE_COMPLETE").touch()
    (pkg / "bundles" / "incomplete-arm0").mkdir()  # no BUNDLE_COMPLETE: never scored
    return pkg


def _dry_run(pkg, **env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("TRIAL_")}
    return subprocess.run(
        ["bash", str(pkg / "scripts" / "judge_trial.sbatch"), "--dry-run", "j"],
        capture_output=True,
        text=True,
        cwd=pkg,
        env={**clean, **env},
        check=False,
    )


def _step4(proc):
    return next(line for line in proc.stdout.splitlines() if line.startswith("# 4."))


def _score_line(proc):
    return next(line for line in proc.stdout.splitlines() if "trial_score.py" in line)


def test_by_default_every_complete_bundle_goes_to_scores(tmp_path):
    pkg = _package(tmp_path)
    proc = _dry_run(pkg)
    assert proc.returncode == 0, proc.stderr
    step4 = _step4(proc)
    assert "3 bundle(s)" in step4
    assert "control-x-arm0 r1-arm0 r2-arm0" in step4
    assert "incomplete-arm0" not in step4
    assert f"into {pkg.resolve()}/scores/j" in step4
    score = _score_line(proc)
    assert "--chunk-size 32" in score
    assert "--max-workers 32" in score


def test_a_bundle_list_partitions_the_work(tmp_path):
    pkg = _package(tmp_path)
    proc = _dry_run(pkg, TRIAL_BUNDLES="r1-arm0, control-*")
    assert proc.returncode == 0, proc.stderr
    step4 = _step4(proc)
    assert "2 bundle(s)" in step4
    assert "control-x-arm0 r1-arm0" in step4
    assert "r2-arm0" not in step4


def test_a_pattern_that_matches_nothing_is_refused(tmp_path):
    pkg = _package(tmp_path)
    proc = _dry_run(pkg, TRIAL_BUNDLES="r1-arm0 nope-*")
    assert proc.returncode == 1
    assert "no bundle matches nope-*" in proc.stderr


def test_the_unscored_folder_scores_into_its_own_root(tmp_path):
    pkg = _package(tmp_path)
    proc = _dry_run(pkg, TRIAL_BUNDLES_DIR="bundles-unscored")
    assert proc.returncode == 0, proc.stderr
    step4 = _step4(proc)
    assert "1 bundle(s)" in step4 and "u1-arm0" in step4
    assert f"into {pkg.resolve()}/scores-unscored/j" in step4


def test_an_empty_workers_knob_leaves_the_manifest_value(tmp_path):
    pkg = _package(tmp_path)
    proc = _dry_run(pkg, TRIAL_MAX_WORKERS="", TRIAL_CHUNK_SIZE="8")
    assert proc.returncode == 0, proc.stderr
    score = _score_line(proc)
    assert "--max-workers" not in score
    assert "--chunk-size 8" in score


def test_a_bundles_dir_with_a_slash_is_refused(tmp_path):
    pkg = _package(tmp_path)
    proc = _dry_run(pkg, TRIAL_BUNDLES_DIR="../elsewhere")
    assert proc.returncode == 1
    assert "TRIAL_BUNDLES_DIR" in proc.stderr
