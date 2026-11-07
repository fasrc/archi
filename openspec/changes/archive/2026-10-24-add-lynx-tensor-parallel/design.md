## Context

Issue #755 is the work order. Anchors are against `origin/dev` at `6aa8b6de`, where the
scorer (#734) and the Slurm job (#735) are merged. The Lynx 70B lines in the job are
comments: four `##SBATCH` lines (`-p gpu_h200`, `--gres=gpu:2`, `--mem=256G`,
`-t 04:00:00`) and two commented settings (`MODEL_ID`, `MODEL_REVISION`). The operator
uses the variant by putting these lines in place of the active ones.

## Goals / Non-Goals

**Goals:** the scorer can split Lynx 70B over two GPUs; the Lynx file says how many GPUs
the model was split over; the 70B variant of the job runs with no change but the swap of
its commented lines.

**Non-Goals:** a cluster run (#737); a pipeline-parallel or multi-node run; showing the GPU
count in the `compare_runs.py` report; a check of the GPU count against the allocation at
run time.

## Decisions

### D1. The GPU count is a keyword argument, default 1, passed to `LLM` every time

`VllmBackend(model, revision, *, tensor_parallel_size=1)` passes
`tensor_parallel_size=tensor_parallel_size` to `LLM` also when it is 1. vLLM's default is
1, so the 8B job loads as before, and a test sees the value in the default case too.
`make_backend(name, model, revision, *, tensor_parallel_size=1)` passes it to
`VllmBackend`; `StubBackend` ignores it. `main` calls the backend factory with
`tensor_parallel_size=` as a keyword. Keyword-only, so a fourth positional argument cannot
be taken for it by mistake.

### D2. A count below 1 is a refusal, before the bundle is read

The check sits with the empty `--model` and `--revision` checks, at the top of the `try`
block of `main`. So the scorer exits 1 (refused), reads no bundle, builds no backend and
writes no file, the same as for an empty `--model`. A value that is not an integer is a
usage error of `argparse` (exit 2). The scorer does not compare the count with the GPUs it
can see: vLLM refuses a count above them when it starts.

### D3. The Lynx file records `tensor_parallel_size`

`score_bundle` takes `tensor_parallel_size` and writes it at the top level of the
document, next to `model_id`, `model_revision` and `backend`. It records the value the
scorer was given, also for the stub backend. The GPU count does not change a verdict in
principle, but it is part of how the run was made. `report.py` checks the identity, rows
and counts of a Lynx file and ignores other keys, so it reads files with and without the
key.

### D4. The job sets the count once, as `GPUS`, beside the model settings

The settings block gets `GPUS="1"`, and the command passes `--tensor-parallel-size
"$GPUS"`. The 70B variant adds `# GPUS="2"` to its commented settings, so the swap that
the header describes covers it. Slurm reads `#SBATCH` lines before the script runs, so the
`--gres=gpu:<N>` line cannot read `GPUS`; a unit test checks that the active
`--gres=gpu:<N>` equals `GPUS`, and that the same holds after the 70B swap. The job
refuses a `GPUS` that is not a whole number of 1 or more before it starts a container, also
in the dry run, so a typo does not first wait for a GPU and convert the image.

Alternative: count the GPUs at run time from `SLURM_GPUS_ON_NODE` or
`CUDA_VISIBLE_DEVICES`. Rejected: the dry run, which runs outside Slurm, could not print
the command that the job runs, and how these variables are set depends on the cluster's
Slurm configuration, which no unit test can check.

## Risks / Trade-offs

- [An operator swaps the 70B `##SBATCH` lines but not `GPUS`] → vLLM loads 141 GB on one
  GPU and fails at model load. Mitigation: the header and the docs name `GPUS` in the swap,
  and `GPUS` sits next to the 70B model id and revision that the operator must swap anyway.
- [vLLM starts a worker process for each GPU] → the worker re-imports the main module
  under the `spawn` method. `score.py` runs `main()` only under
  `if __name__ == "__main__":`, as the vLLM docs require.
