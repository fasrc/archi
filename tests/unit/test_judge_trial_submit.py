"""The judge-trial submit wrapper (dry run): sizing from the judge file, the weights check,
and that one judge's missing weights never blocks the others (fasrc/archi#737, step 5.3)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SUBMIT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "benchmarking"
    / "judge_trial"
    / "submit.sh"
)

ENV = (
    "JUDGE_MODEL_ID=org/{name}\n"
    "JUDGE_MODEL_REVISION=rev1\n"
    "JUDGE_PRECISION=checkpoint\n"
    "JUDGE_GPUS={gpus}\n"
    "JUDGE_MEM=100G\n"
    "JUDGE_TIME=01:00:00\n"
)


def _package(tmp_path, with_weights, without_weights=()):
    pkg = tmp_path / "pkg"
    (pkg / "scripts").mkdir(parents=True)
    shutil.copy(SUBMIT, pkg / "scripts" / "submit.sh")
    (pkg / "judges").mkdir()
    for name in [*with_weights, *without_weights]:
        gpus = 2 if name.startswith("b") else 1
        (pkg / "judges" / f"{name}.env").write_text(ENV.format(name=name, gpus=gpus))
    for name in with_weights:
        repo = pkg / "hf" / "hub" / f"models--org--{name}"
        (repo / "snapshots" / "rev1").mkdir(parents=True)
        (repo / "snapshots" / "rev1" / "config.json").write_text("{}")
        (repo / "blobs").mkdir()
    return pkg


def _run(pkg, *args):
    return subprocess.run(
        ["bash", str(pkg / "scripts" / "submit.sh"), *args],
        capture_output=True,
        text=True,
        cwd=pkg,
        env={**os.environ, "DRY": "1"},
        check=False,
    )


def _sbatch_lines(proc):
    return [line for line in proc.stdout.splitlines() if line.startswith("sbatch ")]


def test_every_ready_judge_gets_one_sbatch_line_sized_from_its_file(tmp_path):
    pkg = _package(tmp_path, ["a-judge", "b-judge"])
    proc = _run(pkg, "--all")
    assert proc.returncode == 0, proc.stderr
    lines = _sbatch_lines(proc)
    assert len(lines) == 2
    assert "--job-name archi-judge-trial-a-judge --gres=gpu:1 --mem=100G" in lines[0]
    assert "--job-name archi-judge-trial-b-judge --gres=gpu:2 --mem=100G" in lines[1]
    assert "-t 01:00:00" in lines[1]
    assert lines[1].rstrip().endswith("judge_trial.sbatch b-judge")


def test_a_judge_without_weights_is_refused_but_the_others_still_go(tmp_path):
    # Glob order is a, b, c: the refused b must not stop c.
    pkg = _package(tmp_path, ["a-judge", "c-judge"], without_weights=["b-judge"])
    proc = _run(pkg, "--all")
    assert proc.returncode == 1
    assert "b-judge: weights at" in proc.stderr
    names = [line.split("--job-name ")[1].split()[0] for line in _sbatch_lines(proc)]
    assert names == ["archi-judge-trial-a-judge", "archi-judge-trial-c-judge"]


def test_a_download_in_progress_counts_as_missing(tmp_path):
    pkg = _package(tmp_path, ["a-judge"])
    (pkg / "hf" / "hub" / "models--org--a-judge" / "blobs" / "x.incomplete").touch()
    proc = _run(pkg, "a-judge")
    assert proc.returncode == 1
    assert "still downloading" in proc.stderr
    assert _sbatch_lines(proc) == []


def test_an_unknown_judge_name_is_reported_and_the_rest_still_go(tmp_path):
    pkg = _package(tmp_path, ["a-judge"])
    proc = _run(pkg, "nope", "a-judge")
    assert proc.returncode == 1
    assert "no " in proc.stderr and "nope.env" in proc.stderr
    assert len(_sbatch_lines(proc)) == 1


def test_all_skips_the_gated_llama_judge(tmp_path):
    pkg = _package(tmp_path, ["a-judge", "llama-3.3-70b"])
    proc = _run(pkg, "--all")
    assert proc.returncode == 0, proc.stderr
    assert len(_sbatch_lines(proc)) == 1
