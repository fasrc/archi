"""The Slurm side of the offloaded RAGAS judge (#733, tasks 4.1-4.3, design D4-D6, D10).

- ``judge_submit.sh``: the submitter that scrontab runs every 15 minutes;
- ``archi_judge.sbatch``: the GPU judge job (vLLM on a private Unix socket);
- ``judge_merge.sbatch``: the CPU merge job;
- ``archi-scorer.def`` and ``build_scorer_image.sh``: the scorer image and its
  ``scorer-identity.json``.

No test needs Slurm, a GPU, Apptainer or the network: stub ``squeue``,
``sbatch``, ``sacct``, ``apptainer`` and ``curl`` executables on ``PATH`` record
each call.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SLURM = REPO / "scripts" / "benchmarking" / "slurm"
SUBMIT = SLURM / "judge_submit.sh"
JOB = SLURM / "archi_judge.sbatch"
MERGE = SLURM / "judge_merge.sbatch"
RECIPE = SLURM / "archi-scorer.def"
BUILD = SLURM / "build_scorer_image.sh"
PYTHON_BIN = str(Path(sys.executable).parent)

_SQUEUE = r"""#!/bin/sh
printf '%s\n' "$*" >> "$STUB/squeue.calls"
[ -f "$STUB/squeue.fail" ] && { echo "squeue: error: cannot reach slurmctld" >&2; exit 1; }
id=""; name=""
while [ $# -gt 0 ]; do
  case "$1" in
    -j) id="$2"; shift 2 ;;
    -n) name="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [ -n "$name" ]; then
  [ -f "$STUB/jobs.$name" ] && cat "$STUB/jobs.$name"
  exit 0
fi
[ -f "$STUB/job.$id" ] && { echo "$id"; exit 0; }
echo "slurm_load_jobs error: Invalid job id specified" >&2
exit 1
"""

_SACCT = r"""#!/bin/sh
id=""
while [ $# -gt 0 ]; do
  case "$1" in -j) id="$2"; shift 2 ;; *) shift ;; esac
done
[ -f "$STUB/sacct.$id" ] && cat "$STUB/sacct.$id"
exit 0
"""

_SBATCH = r"""#!/bin/sh
printf '%s\n' "$*" >> "$STUB/sbatch.calls"
echo 4242
"""

# The judge job's containers: the scorer check and run, vLLM, the merge.
_APPTAINER = r"""#!/bin/bash
printf '%s\n' "$*" >> "$STUB/apptainer.calls"
printf '%s\n' "${APPTAINERENV_VLLM_API_KEY:-}" >> "$STUB/apptainer.keys"
case "$*" in
  *"score.py check"*) exit "$(cat "$STUB/check.rc" 2>/dev/null || echo 0)" ;;
  *"score.py run"*)
    prev=""
    for a in "$@"; do
      [ "$prev" = --sidecar ] && cp "$a" "$STUB/run.sidecar"
      case "$a" in *.sif) sha256sum "$a" | cut -d' ' -f1 > "$STUB/run.image.sha" ;; esac
      prev="$a"
    done
    exit "$(cat "$STUB/run.rc" 2>/dev/null || echo 0)" ;;
  *"score.py write-identity"*)
    out=""; digest=""; prev=""
    for a in "$@"; do
      [ "$prev" = --out ] && out="$a"
      [ "$prev" = --image-digest ] && digest="$a"
      prev="$a"
    done
    printf '{"code_digest": "sha256:c", "image_digest": "%s", "package_digest": "sha256:p"}\n' "$digest" > "$out"
    exit 0 ;;
  *"vllm serve"*)
    prev=""
    for a in "$@"; do [ "$prev" = --uds ] && sock="$a"; prev="$a"; done
    stat -c %a "$(dirname "$sock")" > "$STUB/sockdir.mode"
    echo $$ > "$STUB/vllm.pid"
    if [ -f "$STUB/rebuild" ]; then
      # build_scorer_image.sh while the job runs: the image moves first, then the sidecar.
      printf 'a rebuilt scorer image\n' > "$JUDGE_HOME/archi-scorer.sif.new"
      mv -f "$JUDGE_HOME/archi-scorer.sif.new" "$JUDGE_HOME/archi-scorer.sif"
      printf '{"image_digest": "sha256:rebuilt"}\n' > "$JUDGE_HOME/scorer-identity.json.new"
      mv -f "$JUDGE_HOME/scorer-identity.json.new" "$JUDGE_HOME/scorer-identity.json"
    fi
    exec sleep 300 ;;
  build*)
    out=""
    for a in "$@"; do case "$a" in *.sif*) out="$a" ;; esac; done
    printf 'sif bytes %s\n' "$RANDOM" > "$out"
    exit 0 ;;
esac
exit 0
"""

_CURL = r"""#!/bin/bash
printf '%s\n' "$*" >> "$STUB/curl.calls"
for a in "$@"; do case "$a" in @*) cat "${a#@}" >> "$STUB/curl.header" ;; esac; done
n="$(cat "$STUB/curl.count" 2>/dev/null || echo 0)"; echo $((n + 1)) > "$STUB/curl.count"
[ "$n" -ge "$(cat "$STUB/curl.ready_after" 2>/dev/null || echo 0)" ] && printf 200 || printf 503
"""


@pytest.fixture
def stub(tmp_path):
    """Stub executables and their state folder; returns the state folder."""
    bin_dir = tmp_path / "bin"
    state = tmp_path / "stub"
    bin_dir.mkdir()
    state.mkdir()
    for name, body in (
        ("squeue", _SQUEUE),
        ("sacct", _SACCT),
        ("sbatch", _SBATCH),
        ("apptainer", _APPTAINER),
        ("curl", _CURL),
    ):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    return state


def _env(tmp_path, stub, **extra):
    env = {
        "PATH": f"{tmp_path / 'bin'}:{PYTHON_BIN}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "USER": "tester",
        "STUB": str(stub),
        "JUDGE_HOME": str(tmp_path / "judge"),
        "JUDGE_PYTHON": sys.executable,
        "JUDGE_SCRATCH": str(tmp_path / "scratch"),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    (tmp_path / "tmp").mkdir(exist_ok=True)
    env.update({k: str(v) for k, v in extra.items()})
    return env


def _run(script, tmp_path, stub, *args, **extra):
    return subprocess.run(
        ["bash", str(script), *args],
        env=_env(tmp_path, stub, **extra),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )


def _calls(stub, name):
    path = stub / f"{name}.calls"
    return path.read_text().splitlines() if path.exists() else []


# --- a judge home with a queue -----------------------------------------------------


def _judge_home(tmp_path, **env_overrides):
    home = tmp_path / "judge"
    (home / "queue").mkdir(parents=True)
    values = {
        "JUDGE_MODEL_ID": "meta-llama/Llama-3.3-70B-Instruct",
        "JUDGE_MODEL_REVISION": "rev1",
        "JUDGE_PRECISION": "bf16",
        "JUDGE_BOOTSTRAP_ROWS_PER_MINUTE": "2",
    }
    values.update(env_overrides)
    (home / "judge.env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    (home / "scorer-identity.json").write_text(
        json.dumps({"code_digest": "sha256:code", "package_digest": "sha256:pkg"})
    )
    (home / "archi-scorer.sif").write_text("a scorer image")
    return home


def _bundle(
    home,
    name,
    *,
    arms=1,
    model="meta-llama/Llama-3.3-70B-Instruct",
    markers=(),
    code="sha256:code",
):
    folder = home / "queue" / name
    folder.mkdir()
    basename, index = name.rsplit("-arm", 1)
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "result_basename": basename,
                "arm_index": int(index),
                "arm_count": arms,
                "row_count": 3,
                "judge_model": {"id": model, "revision": "rev1"},
                "code_version": {"digest": code},
                "package_digest": "sha256:pkg",
                "enabled_metrics": ["faithfulness"],
            }
        )
    )
    (folder / "READY").write_text("")
    for marker in markers:
        (folder / marker).write_text("")
    return folder


def _publish_result(home, basename):
    (home / "queue" / "results").mkdir(exist_ok=True)
    (home / "queue" / "results" / f"{basename}.json").write_text("{}")


# --- 4.1 judge_submit.sh -------------------------------------------------------------


def test_the_submitter_submits_nothing_for_an_empty_queue(tmp_path, stub):
    _judge_home(tmp_path)
    proc = _run(SUBMIT, tmp_path, stub)
    assert proc.returncode == 0, proc.stderr
    assert _calls(stub, "sbatch") == []


def test_one_matching_ready_bundle_submits_exactly_one_singleton_job(tmp_path, stub):
    home = _judge_home(tmp_path)
    _bundle(home, "r-20261023_120000-arm0")
    _bundle(home, "s-20261023_120000-arm0")
    proc = _run(SUBMIT, tmp_path, stub)
    assert proc.returncode == 0, proc.stderr
    calls = _calls(stub, "sbatch")
    assert len(calls) == 1
    assert "--dependency=singleton" in calls[0]
    assert calls[0].endswith("archi_judge.sbatch")


def test_a_running_judge_job_means_no_second_submission(tmp_path, stub):
    home = _judge_home(tmp_path)
    _bundle(home, "r-20261023_120000-arm0")
    (stub / "jobs.archi-judge").write_text("1001\n")
    proc = _run(SUBMIT, tmp_path, stub)
    assert proc.returncode == 0, proc.stderr
    assert _calls(stub, "sbatch") == []
    assert "1001" in proc.stdout


def test_bundles_for_another_judge_submit_nothing(tmp_path, stub):
    home = _judge_home(tmp_path)
    _bundle(home, "r-20261023_120000-arm0", model="deepseek-ai/other")
    proc = _run(SUBMIT, tmp_path, stub)
    assert proc.returncode == 0, proc.stderr
    assert _calls(stub, "sbatch") == []


def test_a_failed_squeue_submits_nothing_and_fails(tmp_path, stub):
    home = _judge_home(tmp_path)
    _bundle(home, "r-20261023_120000-arm0")
    (stub / "squeue.fail").write_text("")
    proc = _run(SUBMIT, tmp_path, stub)
    assert proc.returncode != 0
    assert _calls(stub, "sbatch") == []
    assert "squeue" in proc.stderr


def test_a_scored_result_without_merged_submits_one_merge_job(tmp_path, stub):
    home = _judge_home(tmp_path)
    _bundle(home, "r-20261023_120000-arm0", arms=2, markers=("SCORED",))
    _bundle(home, "r-20261023_120000-arm1", arms=2, markers=("SCORED",))
    _publish_result(home, "r-20261023_120000")
    proc = _run(SUBMIT, tmp_path, stub)
    assert proc.returncode == 0, proc.stderr
    calls = _calls(stub, "sbatch")
    assert len(calls) == 1 and calls[0].endswith("judge_merge.sbatch")
    assert "singleton" not in calls[0]


@pytest.mark.parametrize("case", ["merged", "merge job exists", "arm not scored"])
def test_no_merge_job_when_nothing_is_ready_to_merge(tmp_path, stub, case):
    home = _judge_home(tmp_path)
    markers = ("SCORED", "MERGED") if case == "merged" else ("SCORED",)
    _bundle(home, "r-20261023_120000-arm0", arms=2, markers=markers)
    second = () if case == "arm not scored" else markers
    _bundle(home, "r-20261023_120000-arm1", arms=2, markers=second)
    _publish_result(home, "r-20261023_120000")
    if case == "merge job exists":
        (stub / "jobs.archi-judge-merge").write_text("2002\n")
    if case == "arm not scored":
        (stub / "jobs.archi-judge").write_text("1001\n")
    proc = _run(SUBMIT, tmp_path, stub)
    assert proc.returncode == 0, proc.stderr
    assert not any("judge_merge.sbatch" in call for call in _calls(stub, "sbatch"))


def test_status_lists_old_unmatched_stale_and_incompatible_records(tmp_path, stub):
    home = _judge_home(tmp_path)
    old = _bundle(home, "old-20261023_120000-arm0")
    then = time.time() - 30 * 3600
    os.utime(old / "READY", (then, then))
    _bundle(home, "other-20261023_120000-arm0", model="deepseek-ai/other")
    stale = _bundle(home, "stale-20261023_120000-arm0")
    (stale / "claim.1").write_text("1002\n")
    (stub / "sacct.1002").write_text("TIMEOUT\n")
    odd = _bundle(home, "odd-20261023_120000-arm0")
    (odd / "INCOMPATIBLE.0ld").write_text("live pkg Y")
    (home / "queue" / "SCORER_STALE.0123").write_text("sidecar old")
    proc = _run(SUBMIT, tmp_path, stub, "--status")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "old-20261023_120000-arm0: available; READY for 30 hours" in out
    assert "other-20261023_120000-arm0: not matched: waits for judge deepseek" in out
    assert "stale claim of job 1002" in out
    assert "INCOMPATIBLE.0ld: live pkg Y" in out
    assert "SCORER_STALE.0123: sidecar old" in out
    assert _calls(stub, "sbatch") == []


# --- 4.2 archi_judge.sbatch ------------------------------------------------------------


def _directives(script):
    return [
        line for line in script.read_text().splitlines() if line.startswith("#SBATCH ")
    ]


def test_the_judge_job_header_has_the_name_requeue_and_append():
    lines = _directives(JOB)
    assert "#SBATCH -J archi-judge" in lines
    assert "#SBATCH --requeue" in lines
    assert "#SBATCH --open-mode=append" in lines
    for option in ("-p ", "--gres=gpu:", "-t "):
        assert any(line.startswith(f"#SBATCH {option}") for line in lines), option


def test_the_partition_and_gpus_default_to_the_designs_first_option():
    lines = _directives(JOB)
    assert "#SBATCH -p gpu_h200" in lines
    assert "#SBATCH --gres=gpu:2" in lines
    assert "#729" in JOB.read_text()


def test_the_dry_run_prints_every_command_and_starts_nothing(tmp_path, stub):
    _judge_home(tmp_path, JUDGE_SERVER_ARGS="--max-model-len 16384")
    proc = _run(JOB, tmp_path, stub, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "score.py check" in out and "score.py run" in out
    assert re.search(r"vllm serve meta-llama/Llama-3\.3-70B-Instruct ", out)
    assert "--served-model-name meta-llama/Llama-3.3-70B-Instruct" in out
    assert "--revision rev1" in out and "--dtype bfloat16" in out
    assert "--max-model-len 16384" in out
    assert "--uds " in out and "--host" not in out and "--port" not in out
    assert "--dependency=afterok:" in out and "judge_merge.sbatch" in out
    assert _calls(stub, "apptainer") == [] and _calls(stub, "sbatch") == []


def test_the_dry_run_works_before_judge_env_exists(tmp_path, stub):
    proc = _run(JOB, tmp_path, stub, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    # Shell-quoted, as every printed command is.
    assert r"vllm serve \<JUDGE_MODEL_ID\>" in proc.stdout


def test_an_unknown_precision_is_refused(tmp_path, stub):
    _judge_home(tmp_path, JUDGE_PRECISION="int3")
    proc = _run(JOB, tmp_path, stub, "--dry-run")
    assert proc.returncode != 0 and "int3" in proc.stderr


def test_the_job_refuses_to_run_outside_slurm(tmp_path, stub):
    _judge_home(tmp_path)
    proc = _run(JOB, tmp_path, stub)
    assert proc.returncode != 0 and "Slurm" in proc.stderr
    assert _calls(stub, "apptainer") == []


VLLM_BYTES = b"a vllm image"
VLLM_DIGEST = "sha256:" + hashlib.sha256(VLLM_BYTES).hexdigest()


def _job(
    tmp_path, stub, check_rc=0, run_rc=0, judge_env=None, vllm=VLLM_BYTES, **extra
):
    _judge_home(tmp_path, **(judge_env or {}))
    if vllm is not None:
        (tmp_path / "scratch").mkdir(exist_ok=True)
        (tmp_path / "scratch" / "vllm-openai.sif").write_bytes(vllm)
    (stub / "check.rc").write_text(str(check_rc))
    (stub / "run.rc").write_text(str(run_rc))
    return _run(
        JOB,
        tmp_path,
        stub,
        SLURM_JOB_ID="9001",
        ARCHI_REPO=str(REPO),
        JUDGE_READY_POLL_SECONDS="0",
        **extra,
    )


def test_a_job_with_no_work_exits_before_vllm_starts(tmp_path, stub):
    proc = _job(tmp_path, stub, check_rc=3)
    assert proc.returncode == 0, proc.stderr
    calls = _calls(stub, "apptainer")
    assert len(calls) == 1 and "score.py check" in calls[0]
    assert "--job-id 9001" in calls[0]
    assert _calls(stub, "sbatch") == []


def test_a_stale_sidecar_exits_non_zero_before_vllm_starts(tmp_path, stub):
    proc = _job(tmp_path, stub, check_rc=2)
    assert proc.returncode != 0
    assert not any("vllm serve" in call for call in _calls(stub, "apptainer"))
    assert "SCORER_STALE" in proc.stderr


def test_a_job_serves_on_a_private_socket_scores_merges_and_stops_vllm(tmp_path, stub):
    (stub / "curl.ready_after").write_text("2")
    proc = _job(tmp_path, stub)
    assert proc.returncode == 0, proc.stderr
    calls = _calls(stub, "apptainer")
    order = [
        next(i for i, c in enumerate(calls) if needle in c)
        for needle in ("score.py check", "vllm serve", "score.py run")
    ]
    assert order == sorted(order)
    serve = next(c for c in calls if "vllm serve" in c)
    assert "--host" not in serve and "--port" not in serve
    assert (stub / "sockdir.mode").read_text().strip() == "700"
    run = next(c for c in calls if "score.py run" in c)
    socket = re.search(r"--uds (\S+)", serve).group(1)
    assert f"--socket {socket}" in run
    # It waited for /v1/models through the socket.
    assert int((stub / "curl.count").read_text()) == 3
    assert all("--unix-socket" in c and "/v1/models" in c for c in _calls(stub, "curl"))
    # The merge job is submitted after this job succeeds.
    (merge,) = _calls(stub, "sbatch")
    assert "--dependency=afterok:9001" in merge and merge.endswith("judge_merge.sbatch")
    # The trap stopped vLLM and removed the socket folder.
    pid = int((stub / "vllm.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not Path(socket).parent.exists()


def test_the_api_key_is_in_the_environment_never_on_a_command_line(tmp_path, stub):
    proc = _job(tmp_path, stub)
    assert proc.returncode == 0, proc.stderr
    keys = {k for k in (stub / "apptainer.keys").read_text().splitlines() if k}
    assert len(keys) == 1
    (key,) = keys
    assert len(key) >= 32
    for name in ("apptainer", "curl", "sbatch"):
        assert all(key not in call for call in _calls(stub, name)), name
    assert f"Bearer {key}" in (stub / "curl.header").read_text()


def test_a_server_that_never_gets_ready_fails_the_job_and_is_stopped(tmp_path, stub):
    (stub / "curl.ready_after").write_text("1000")
    proc = _job(tmp_path, stub, JUDGE_READY_TIMEOUT="0")
    assert proc.returncode != 0 and "ready" in proc.stderr
    assert not any("score.py run" in c for c in _calls(stub, "apptainer"))
    assert _calls(stub, "sbatch") == []
    with pytest.raises(ProcessLookupError):
        os.kill(int((stub / "vllm.pid").read_text()), 0)


def test_a_failed_scorer_submits_no_merge(tmp_path, stub):
    proc = _job(tmp_path, stub, run_rc=1)
    assert proc.returncode != 0
    assert _calls(stub, "sbatch") == []


def _image_digest(tmp_path):
    image = tmp_path / "judge" / "archi-scorer.sif"
    return "sha256:" + hashlib.sha256(image.read_bytes()).hexdigest()


#: The digest of the image that ``_judge_home`` writes.
IMAGE_DIGEST = "sha256:" + hashlib.sha256(b"a scorer image").hexdigest()


def test_the_job_pins_one_image_and_sidecar_and_passes_the_image_digest(tmp_path, stub):
    digest = IMAGE_DIGEST
    proc = _job(tmp_path, stub)
    assert proc.returncode == 0, proc.stderr
    calls = _calls(stub, "apptainer")
    pinned = str(tmp_path / "judge" / ".archi-scorer.9001.sif")
    for needle in ("score.py check", "score.py run"):
        (call,) = [c for c in calls if needle in c]
        assert f"--image-digest {digest}" in call
        assert f" {pinned} " in call
        assert re.search(
            r"--sidecar \S+/archi-judge\.9001\.\w+/scorer-identity\.json", call
        )
    # The pinned files go away with the job.
    assert not Path(pinned).exists()


def test_a_rebuild_during_the_job_keeps_the_pinned_pair(tmp_path, stub):
    digest = IMAGE_DIGEST
    sidecar = json.dumps({"code_digest": "sha256:code", "package_digest": "sha256:pkg"})
    (stub / "rebuild").write_text("")
    proc = _job(tmp_path, stub)
    assert proc.returncode == 0, proc.stderr
    # The rebuild replaced both files while vLLM started; the scorer still ran
    # the image the check hashed, with the sidecar the check read.
    assert _image_digest(tmp_path) != digest
    assert "sha256:" + (stub / "run.image.sha").read_text().strip() == digest
    assert (stub / "run.sidecar").read_text() == sidecar
    (run,) = [c for c in _calls(stub, "apptainer") if "score.py run" in c]
    assert f"--image-digest {digest}" in run


def test_the_job_serves_a_pinned_vllm_sif_and_records_its_measured_digest(
    tmp_path, stub
):
    proc = _job(tmp_path, stub)
    assert proc.returncode == 0, proc.stderr
    calls = _calls(stub, "apptainer")
    pinned = str(tmp_path / "scratch" / ".vllm-openai.9001.sif")
    (serve,) = [c for c in calls if "vllm serve" in c]
    assert f" {pinned} vllm serve " in serve
    (run,) = [c for c in calls if "score.py run" in c]
    assert f"--server-image-digest {VLLM_DIGEST}" in run
    assert not Path(pinned).exists()


def test_a_judge_env_digest_of_another_vllm_image_is_refused_before_serving(
    tmp_path, stub
):
    proc = _job(tmp_path, stub, judge_env={"JUDGE_IMAGE_DIGEST": "sha256:other"})
    assert proc.returncode != 0 and "JUDGE_IMAGE_DIGEST" in proc.stderr
    assert VLLM_DIGEST in proc.stderr
    assert not any("vllm serve" in c for c in _calls(stub, "apptainer"))


def test_a_judge_env_digest_of_this_vllm_image_is_accepted(tmp_path, stub):
    proc = _job(tmp_path, stub, judge_env={"JUDGE_IMAGE_DIGEST": VLLM_DIGEST})
    assert proc.returncode == 0, proc.stderr


def test_a_job_without_the_vllm_sif_starts_no_server(tmp_path, stub):
    proc = _job(tmp_path, stub, vllm=None)
    assert proc.returncode != 0 and "apptainer pull" in proc.stderr
    assert not any("vllm serve" in c for c in _calls(stub, "apptainer"))


def test_the_dry_run_names_the_measured_vllm_digest(tmp_path, stub):
    _judge_home(tmp_path)
    proc = _run(JOB, tmp_path, stub, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "--server-image-digest" in proc.stdout
    assert "vllm-openai.sif" in proc.stdout and "docker://" not in proc.stdout


def test_a_job_without_the_scorer_image_starts_nothing(tmp_path, stub):
    _judge_home(tmp_path)
    (tmp_path / "judge" / "archi-scorer.sif").unlink()
    proc = _run(JOB, tmp_path, stub, SLURM_JOB_ID="9001", ARCHI_REPO=str(REPO))
    assert proc.returncode != 0 and "build_scorer_image.sh" in proc.stderr
    assert _calls(stub, "apptainer") == []


def test_a_job_outside_the_repo_root_starts_nothing(tmp_path, stub):
    _judge_home(tmp_path)
    proc = _run(JOB, tmp_path, stub, SLURM_JOB_ID="9001", ARCHI_REPO=str(tmp_path))
    assert proc.returncode != 0 and "repo root" in proc.stderr
    assert _calls(stub, "apptainer") == []


# --- 4.3 judge_merge.sbatch -------------------------------------------------------------


def test_the_merge_job_is_cpu_only_and_named():
    lines = _directives(MERGE)
    assert "#SBATCH -J archi-judge-merge" in lines
    assert not any("gpu" in line for line in lines)


def test_the_merge_dry_run_prints_the_merge_and_starts_nothing(tmp_path, stub):
    _judge_home(tmp_path)
    proc = _run(MERGE, tmp_path, stub, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert (
        "merge.py --queue" in proc.stdout
        and str(tmp_path / "judge" / "queue") in proc.stdout
    )
    # The merge image merges only the results of its own code.
    assert "--own-code-only" in proc.stdout
    assert _calls(stub, "apptainer") == []


def test_no_merge_job_for_a_result_of_other_code_than_the_scorer(tmp_path, stub):
    # The merge job runs the configured scorer image, which merges only results
    # of its own code; a job for another result would merge nothing on every tick.
    home = _judge_home(tmp_path)
    _bundle(home, "r-20261023_120000-arm0", markers=("SCORED",), code="sha256:old")
    _publish_result(home, "r-20261023_120000")
    proc = _run(SUBMIT, tmp_path, stub)
    assert proc.returncode == 0, proc.stderr
    assert not any("judge_merge.sbatch" in call for call in _calls(stub, "sbatch"))


def test_the_merge_job_runs_the_merge_in_the_scorer_image(tmp_path, stub):
    _judge_home(tmp_path)
    proc = _run(MERGE, tmp_path, stub, SLURM_JOB_ID="9002")
    assert proc.returncode == 0, proc.stderr
    (call,) = _calls(stub, "apptainer")
    assert "archi-scorer.sif" in call and "merge.py --queue" in call


# --- the scorer image -------------------------------------------------------------------


def test_the_recipe_adds_only_the_judge_scripts_to_the_benchmark_image():
    text = RECIPE.read_text()
    assert re.search(r"^Bootstrap: docker-archive$", text, re.MULTILINE)
    assert re.search(r"^From: \{\{ benchmark_image \}\}$", text, re.MULTILINE)
    for script in ("score.py", "judge_queue.py", "merge.py"):
        assert (
            f"scripts/benchmarking/judge/{script} /root/archi/scripts/benchmarking/judge/{script}"
            in text
        )
    # Anything installed would change the package digest, and no bundle of the
    # run would match the scorer any more.
    assert "pip" not in text and "%post" not in text


def test_the_build_dry_run_prints_the_build_and_the_identity_and_runs_nothing(
    tmp_path, stub
):
    proc = _run(BUILD, tmp_path, stub, "--dry-run", "bench.tar", str(tmp_path / "out"))
    assert proc.returncode == 0, proc.stderr
    assert "apptainer build" in proc.stdout and "benchmark_image=" in proc.stdout
    assert "write-identity" in proc.stdout and "--image-digest" in proc.stdout
    assert _calls(stub, "apptainer") == []


def test_the_build_writes_the_image_and_its_identity_with_the_image_digest(
    tmp_path, stub
):
    tar = tmp_path / "bench.tar"
    tar.write_text("docker save output")
    out = tmp_path / "out"
    proc = _run(BUILD, tmp_path, stub, str(tar), str(out))
    assert proc.returncode == 0, proc.stderr
    image = out / "archi-scorer.sif"
    sidecar = json.loads((out / "scorer-identity.json").read_text())
    digest = "sha256:" + hashlib.sha256(image.read_bytes()).hexdigest()
    assert sidecar["image_digest"] == digest
    build, identity = _calls(stub, "apptainer")
    assert f"benchmark_image={tar.resolve()}" in build and str(RECIPE) in build
    assert "score.py write-identity" in identity
    assert not list(out.glob("*.new"))


def test_the_build_refuses_a_missing_image_archive(tmp_path, stub):
    proc = _run(
        BUILD, tmp_path, stub, str(tmp_path / "nope.tar"), str(tmp_path / "out")
    )
    assert proc.returncode != 0 and "nope.tar" in proc.stderr
    assert _calls(stub, "apptainer") == []
