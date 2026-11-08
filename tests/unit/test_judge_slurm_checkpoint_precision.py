"""``JUDGE_PRECISION=checkpoint`` serves pre-quantized weights with no quantization flag
(add-inline-bundle-judge-mode, D8). Uses the stub harness of ``test_judge_slurm``."""

from __future__ import annotations

from tests.unit.test_judge_slurm import stub  # noqa: F401  (fixture)
from tests.unit.test_judge_slurm import JOB, _calls, _judge_home, _run


def _serve_line(stdout):
    lines = [line for line in stdout.splitlines() if "vllm serve" in line]
    assert len(lines) == 1, stdout
    return lines[0]


def test_checkpoint_precision_emits_dtype_auto_and_no_quantization(tmp_path, stub):
    _judge_home(tmp_path, JUDGE_PRECISION="checkpoint")

    proc = _run(JOB, tmp_path, stub, "--dry-run")

    assert proc.returncode == 0, proc.stderr
    serve = _serve_line(proc.stdout)
    assert "--dtype auto" in serve
    assert "--quantization" not in serve
    assert _calls(stub, "apptainer") == []


def test_fp8_still_asks_vllm_to_quantize(tmp_path, stub):
    _judge_home(tmp_path, JUDGE_PRECISION="fp8")

    proc = _run(JOB, tmp_path, stub, "--dry-run")

    assert proc.returncode == 0, proc.stderr
    assert "--quantization fp8" in _serve_line(proc.stdout)


def test_an_unknown_precision_names_the_four_allowed_values(tmp_path, stub):
    _judge_home(tmp_path, JUDGE_PRECISION="int3")

    proc = _run(JOB, tmp_path, stub, "--dry-run")

    assert proc.returncode != 0
    for word in ("int3", "bf16", "fp16", "fp8", "checkpoint"):
        assert word in proc.stderr
