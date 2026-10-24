"""scripts/benchmarking/slurm/lynx_score.sbatch: the Lynx Slurm job (#735, design D11).

No test needs Slurm, a GPU, Apptainer or the network. Stub ``apptainer``, ``sbatch``
and ``squeue`` executables on ``PATH`` record each call, so a test can prove that the
job started nothing, or that it started exactly the command that ``--dry-run`` prints.
"""

import re
import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "benchmarking" / "slurm" / "lynx_score.sbatch"
STUBS = ("apptainer", "sbatch", "squeue")
PINNED_IMAGE = re.compile(
    r"^docker://vllm/vllm-openai:v\d+\.\d+\.\d+(-[A-Za-z0-9.]+)?$"
)


def _setting(name):
    """The value of a ``NAME="value"`` setting line in the script."""
    match = re.search(rf'^{name}="([^"]*)"$', SCRIPT.read_text(), re.MULTILINE)
    assert match, f"{SCRIPT.name} has no {name} setting"
    return match.group(1)


def _sbatch_lines():
    """The active ``#SBATCH`` directives, which Slurm reads (not ``##SBATCH``)."""
    return [
        line for line in SCRIPT.read_text().splitlines() if line.startswith("#SBATCH ")
    ]


def _stub_bin(tmp_path):
    """Stub executables that record their arguments; made once for each test."""
    bin_dir = tmp_path / "bin"
    calls = tmp_path / "calls"
    if bin_dir.exists():
        return bin_dir, calls
    bin_dir.mkdir()
    calls.mkdir()
    for name in STUBS:
        stub = bin_dir / name
        stub.write_text(
            "#!/bin/bash\n"
            f'printf "%s\\0" "$@" > "{calls}/{name}"\n'
            f'printf "%s" "${{APPTAINER_CACHEDIR:-}}" > "{calls}/{name}.cachedir"\n'
        )
        stub.chmod(0o755)
    return bin_dir, calls


def _run_bundle(tmp_path, name="run-a-arm0", marker="BUNDLE_COMPLETE", result=True):
    """A run's output folder with one bundle folder under ``judge_bundles/``."""
    out = tmp_path / "out"
    bundle = out / "judge_bundles" / name
    bundle.mkdir(parents=True)
    if marker:
        (bundle / marker).write_text("")
    if result:
        (out / "run-a.json").write_text("{}")
    return out.resolve(), bundle.resolve()


def _run(tmp_path, *args, script=SCRIPT, **env):
    """Run the script with stubs first on PATH; an env value of None removes the key."""
    bin_dir, calls = _stub_bin(tmp_path)
    run_env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "USER": "tester",
        "LYNX_SCRATCH": str(tmp_path / "scratch"),
    }
    run_env.update({k: v for k, v in env.items() if v is not None})
    for key in [k for k, v in env.items() if v is None]:
        run_env.pop(key, None)
    proc = subprocess.run(
        ["bash", str(script), *map(str, args)],
        capture_output=True,
        text=True,
        env=run_env,
        cwd=tmp_path,
        check=False,
    )
    return proc, calls


def _called(calls):
    return sorted(p.name for p in calls.iterdir() if not p.name.endswith(".cachedir"))


def _expected_command(bundle, out, scratch):
    return [
        "apptainer",
        "exec",
        "--nv",
        "--bind",
        str(REPO),
        "--bind",
        str(bundle),
        "--bind",
        str(out),
        "--bind",
        str(scratch),
        "--pwd",
        str(REPO),
        "--env",
        f"HF_HOME={scratch}/hf",
        "--env",
        f"XDG_CACHE_HOME={scratch}/cache",
        "--env",
        "PYTHONNOUSERSITE=1",
        _setting("IMAGE"),
        "python3",
        "-m",
        "scripts.benchmarking.lynx.score",
        str(bundle),
        "--model",
        _setting("MODEL_ID"),
        "--revision",
        _setting("MODEL_REVISION"),
        "--out-dir",
        str(out),
        "--backend",
        "vllm",
        "--tensor-parallel-size",
        _setting("GPUS"),
    ]


def _swap_70b(text):
    """The script with its commented Lynx 70B lines in place of the active ones.

    This is the swap that the script's header tells the operator to make: each
    ``##SBATCH`` line replaces the active ``#SBATCH`` line of the same option, and
    each commented ``# NAME="value"`` setting replaces the active ``NAME=`` line.
    """
    lines = text.splitlines()
    directives = [line[1:] for line in lines if line.startswith("##SBATCH ")]
    settings = [line[2:] for line in lines if re.fullmatch(r'# [A-Z_]+="[^"]*"', line)]
    used = []

    def option(directive):
        return re.split(r"[ =]", directive.split(" ", 1)[1], maxsplit=1)[0]

    def swap(line, variants, key):
        match = [v for v in variants if key(v) == key(line)]
        used.extend(match)
        return match[0] if match else line

    swapped = []
    for line in lines:
        if line.startswith("#SBATCH "):
            line = swap(line, directives, option)
        elif re.fullmatch(r'[A-Z_]+="[^"]*"', line):
            line = swap(line, settings, lambda x: x.split("=", 1)[0])
        swapped.append(line)
    assert sorted(used) == sorted(directives + settings), "a 70B line swaps nothing"
    return "\n".join(swapped) + "\n"


# --- 8.1 dry run -------------------------------------------------------------


def test_dry_run_prints_the_apptainer_command_and_starts_nothing(tmp_path):
    out, bundle = _run_bundle(tmp_path)
    proc, calls = _run(tmp_path, "--dry-run", bundle)
    assert proc.returncode == 0, proc.stderr
    printed = shlex.split(proc.stdout.strip())
    assert printed == _expected_command(bundle, out, tmp_path / "scratch")
    assert _called(calls) == []
    assert not (tmp_path / "scratch").exists()


def test_the_settings_name_the_8b_model_at_a_pinned_revision():
    assert _setting("MODEL_ID") == "PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct"
    assert re.fullmatch(r"[0-9a-f]{40}", _setting("MODEL_REVISION"))


def test_the_8b_job_runs_the_model_on_one_gpu():
    assert _setting("GPUS") == "1"


@pytest.mark.parametrize("variant", ["8b", "70b"])
def test_the_gres_line_asks_for_the_gpus_that_the_model_is_split_over(variant):
    text = SCRIPT.read_text()
    if variant == "70b":
        text = _swap_70b(text)
    gpus = re.search(r'^GPUS="(\d+)"$', text, re.MULTILINE).group(1)
    assert f"#SBATCH --gres=gpu:{gpus}" in text.splitlines()


def test_the_70b_variant_splits_the_model_over_two_gpus(tmp_path):
    variant = tmp_path / "scripts" / "benchmarking" / "slurm" / SCRIPT.name
    variant.parent.mkdir(parents=True)
    variant.write_text(_swap_70b(SCRIPT.read_text()))
    _, bundle = _run_bundle(tmp_path)
    proc, calls = _run(tmp_path, "--dry-run", bundle, script=variant)
    assert proc.returncode == 0, proc.stderr
    printed = shlex.split(proc.stdout.strip())
    model = printed[printed.index("--model") + 1]
    assert model == "PatronusAI/Llama-3-Patronus-Lynx-70B-Instruct"
    revision = printed[printed.index("--revision") + 1]
    assert revision == "503b9b9469aec7be01144517e697a20bd966f27a"
    assert printed[printed.index("--tensor-parallel-size") + 1] == "2"
    directives = [
        x for x in variant.read_text().splitlines() if x.startswith("#SBATCH ")
    ]
    assert "#SBATCH -p gpu_h200" in directives
    assert "#SBATCH --gres=gpu:2" in directives
    assert _called(calls) == []


# --- 8.2 refusals ------------------------------------------------------------


def test_a_missing_bundle_folder_is_refused(tmp_path):
    proc, calls = _run(tmp_path, "--dry-run", tmp_path / "no-such-run-arm0")
    assert proc.returncode != 0
    assert "not a folder" in proc.stderr
    assert _called(calls) == []


@pytest.mark.parametrize("marker", [None, "READY"])
def test_a_bundle_with_no_bundle_complete_is_refused(tmp_path, marker):
    _, bundle = _run_bundle(tmp_path, marker=marker)
    proc, calls = _run(tmp_path, "--dry-run", bundle)
    assert proc.returncode != 0
    assert "BUNDLE_COMPLETE" in proc.stderr
    assert proc.stdout == ""
    assert _called(calls) == []


@pytest.mark.parametrize("gpus", ["0", "", "two", "1.5"])
def test_a_gpu_count_that_is_not_a_whole_number_of_one_or_more_is_refused(
    tmp_path, gpus
):
    variant = tmp_path / "scripts" / "benchmarking" / "slurm" / SCRIPT.name
    variant.parent.mkdir(parents=True)
    text = SCRIPT.read_text()
    assert text.count('\nGPUS="1"\n') == 1
    variant.write_text(text.replace('\nGPUS="1"\n', f'\nGPUS="{gpus}"\n'))
    _, bundle = _run_bundle(tmp_path)
    proc, calls = _run(tmp_path, "--dry-run", bundle, script=variant)
    assert proc.returncode != 0
    assert "GPUS" in proc.stderr
    assert proc.stdout == ""
    assert _called(calls) == []


def test_a_folder_name_with_no_arm_index_is_refused(tmp_path):
    _, bundle = _run_bundle(tmp_path, name="run-a")
    proc, calls = _run(tmp_path, "--dry-run", bundle)
    assert proc.returncode != 0
    assert "<basename>-arm<N>" in proc.stderr
    assert _called(calls) == []


def test_an_existing_lynx_file_is_refused(tmp_path):
    out, bundle = _run_bundle(tmp_path)
    (out / "run-a-arm0.lynx.json").write_text("{}")
    proc, calls = _run(tmp_path, "--dry-run", bundle)
    assert proc.returncode != 0
    assert "run-a-arm0.lynx.json" in proc.stderr
    assert _called(calls) == []


# --- 8.3 out dir -------------------------------------------------------------


def test_the_out_dir_is_found_above_a_nested_bundle_dir(tmp_path):
    out = tmp_path / "out"
    bundle = out / "a" / "b" / "run-a-arm1"
    bundle.mkdir(parents=True)
    (bundle / "BUNDLE_COMPLETE").write_text("")
    (out / "run-a.json").write_text("{}")
    proc, _ = _run(tmp_path, "--dry-run", bundle)
    assert proc.returncode == 0, proc.stderr
    printed = shlex.split(proc.stdout.strip())
    assert printed[printed.index("--out-dir") + 1] == str(out.resolve())


def test_the_second_argument_is_the_out_dir(tmp_path):
    _, bundle = _run_bundle(tmp_path, result=False)
    results = tmp_path / "results"
    results.mkdir()
    proc, _ = _run(tmp_path, "--dry-run", bundle, results)
    assert proc.returncode == 0, proc.stderr
    printed = shlex.split(proc.stdout.strip())
    assert printed[printed.index("--out-dir") + 1] == str(results.resolve())


def test_no_result_json_and_no_out_dir_is_refused(tmp_path):
    _, bundle = _run_bundle(tmp_path, result=False)
    proc, calls = _run(tmp_path, "--dry-run", bundle)
    assert proc.returncode != 0
    assert "run-a.json" in proc.stderr
    assert _called(calls) == []


def test_an_out_dir_inside_the_bundle_is_refused(tmp_path):
    _, bundle = _run_bundle(tmp_path)
    proc, calls = _run(tmp_path, "--dry-run", bundle, bundle)
    assert proc.returncode != 0
    assert "inside the bundle" in proc.stderr
    assert _called(calls) == []


# --- 8.4 partition, requeue and image ----------------------------------------


@pytest.mark.parametrize("partition", ["serial_requeue", "gpu_requeue"])
def test_the_script_names_no_requeue_partition(partition):
    assert partition not in SCRIPT.read_text()


def test_slurm_never_requeues_the_job():
    assert "#SBATCH --no-requeue" in _sbatch_lines()
    assert not any("--requeue" in line for line in _sbatch_lines())


def test_the_active_directives_ask_for_one_full_gpu():
    lines = _sbatch_lines()
    assert "#SBATCH -J archi-lynx" in lines
    assert "#SBATCH -p gpu" in lines
    assert "#SBATCH --gres=gpu:1" in lines
    assert any(re.fullmatch(r"#SBATCH -t \d\d:\d\d:\d\d", line) for line in lines)


def test_the_70b_variant_is_commented_out_and_can_load_the_model():
    text = SCRIPT.read_text()
    assert "##SBATCH -p gpu_h200" in text
    assert "##SBATCH --gres=gpu:2" in text
    assert '# MODEL_ID="PatronusAI/Llama-3-Patronus-Lynx-70B-Instruct"' in text
    assert '# GPUS="2"' in text
    assert not any("gpu_h200" in line for line in _sbatch_lines())
    # The NOTE that the variant cannot load the model went away with #755.
    assert "cannot load" not in text
    assert "#755" not in text


def test_the_image_is_pinned_to_an_exact_tag():
    assert PINNED_IMAGE.match(_setting("IMAGE"))


@pytest.mark.parametrize(
    "image",
    [
        "docker://vllm/vllm-openai:latest",
        "docker://vllm/vllm-openai",
        "docker://vllm/vllm-openai:v0.31",
        "docker://vllm/vllm-openai:nightly",
    ],
)
def test_the_tag_check_fails_on_a_moving_or_missing_tag(image):
    assert not PINNED_IMAGE.match(image)


# --- 8.5 the real run --------------------------------------------------------


def test_a_real_run_outside_a_slurm_job_is_refused(tmp_path):
    _, bundle = _run_bundle(tmp_path)
    proc, calls = _run(tmp_path, bundle, SLURM_JOB_ID=None)
    assert proc.returncode != 0
    assert "not in a Slurm job" in proc.stderr
    assert _called(calls) == []


def test_a_job_submitted_outside_the_repo_root_is_refused(tmp_path):
    _, bundle = _run_bundle(tmp_path)
    proc, calls = _run(
        tmp_path, bundle, SLURM_JOB_ID="42", SLURM_SUBMIT_DIR=str(tmp_path)
    )
    assert proc.returncode != 0
    assert "repo root" in proc.stderr
    assert _called(calls) == []


def test_a_job_with_no_lab_and_no_scratch_folder_is_refused(tmp_path):
    _, bundle = _run_bundle(tmp_path)
    proc, calls = _run(
        tmp_path,
        bundle,
        SLURM_JOB_ID="42",
        SLURM_SUBMIT_DIR=str(REPO),
        LYNX_SCRATCH=None,
    )
    assert proc.returncode != 0
    assert "LYNX_LAB" in proc.stderr
    assert _called(calls) == []


def test_a_dry_run_with_no_lab_warns_and_shows_the_default_scratch_folder(tmp_path):
    _, bundle = _run_bundle(tmp_path)
    proc, calls = _run(tmp_path, "--dry-run", bundle, LYNX_SCRATCH=None)
    assert proc.returncode == 0, proc.stderr
    assert "LYNX_LAB" in proc.stderr
    assert "HF_HOME=/n/netscratch//Lab/tester/archi-lynx/hf" in proc.stdout
    assert _called(calls) == []


def test_the_lab_sets_the_scratch_folder_under_netscratch(tmp_path):
    _, bundle = _run_bundle(tmp_path)
    proc, _ = _run(tmp_path, "--dry-run", bundle, LYNX_SCRATCH=None, LYNX_LAB="x_lab")
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    printed = shlex.split(proc.stdout.strip())
    assert "HF_HOME=/n/netscratch/x_lab/Lab/tester/archi-lynx/hf" in printed


def test_a_job_runs_the_command_that_the_dry_run_prints(tmp_path):
    _, bundle = _run_bundle(tmp_path)
    dry, _ = _run(tmp_path, "--dry-run", bundle)
    proc, calls = _run(tmp_path, bundle, SLURM_JOB_ID="42", SLURM_SUBMIT_DIR=str(REPO))
    assert proc.returncode == 0, proc.stderr
    assert _called(calls) == ["apptainer"]
    argv = (calls / "apptainer").read_text().split("\0")[:-1]
    assert ["apptainer", *argv] == shlex.split(dry.stdout.strip())
    scratch = tmp_path / "scratch"
    assert (scratch / "hf").is_dir()
    # vLLM's compile cache follows XDG_CACHE_HOME; the home folder is bound in.
    assert (scratch / "cache").is_dir()
    assert (calls / "apptainer.cachedir").read_text() == str(scratch / "apptainer")
    assert (scratch / "apptainer").is_dir()


def test_a_usage_error_exits_non_zero(tmp_path):
    proc, calls = _run(tmp_path, "--dry-run")
    assert proc.returncode != 0
    assert "usage" in proc.stderr
    assert _called(calls) == []
