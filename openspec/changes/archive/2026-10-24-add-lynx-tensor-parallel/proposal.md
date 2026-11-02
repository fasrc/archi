## Why

Lynx 70B (`PatronusAI/Llama-3-Patronus-Lynx-70B-Instruct`) cannot score a judge bundle.
At revision `503b9b9469aec7be01144517e697a20bd966f27a` its weights are 141,107,497,136
bytes, and one H200 has 140 GB, so the weights do not fit on one GPU. The scorer's vLLM
backend builds `LLM(model=..., revision=..., tokenizer_revision=...)`
(`scripts/benchmarking/lynx/backends.py:57`) with no `tensor_parallel_size`, so vLLM puts
the whole model on one GPU and the job fails at model load. The Slurm job
`scripts/benchmarking/slurm/lynx_score.sbatch` has a commented 70B variant (`gpu_h200`, two
GPUs) with a NOTE that it cannot load the model yet (#755).

vLLM splits one model over GPUs only through the `tensor_parallel_size` argument of `LLM`
(vLLM docs, "Parallelism and Scaling"; `vllm/entrypoints/llm.py:192` at `v0.31.0`, the
image tag the job pins). No environment variable sets it.

## What Changes

- `VllmBackend` and `make_backend` take `tensor_parallel_size` (default 1) and pass it to
  `LLM`. The default keeps the 8B job as it is: vLLM's own default is also 1.
- The scorer CLI takes `--tensor-parallel-size N` (default 1). It refuses a value below 1
  with exit 1 before it reads the bundle or builds a backend. It records
  `tensor_parallel_size` in the Lynx file, so a reader can see how the run was made. The
  readers of the Lynx file (`scripts/benchmarking/lynx/report.py`, `compare_runs.py`) do
  not check the key set, so they read old and new files the same way.
- `lynx_score.sbatch` gets a `GPUS` setting (1) that the job passes as
  `--tensor-parallel-size`. The commented 70B variant sets `GPUS="2"` next to its model id
  and revision, and the NOTE that points to #755 goes away.
- The "Lynx faithfulness (offline)" section of `docs/docs/benchmarking.md` gives the 70B
  settings and the new option.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `lynx-faithfulness-scoring`: the scorer takes a GPU count for vLLM and records it in the
  Lynx file; the Slurm job passes its GPU count setting to the scorer; the docs give the 70B
  settings.

## Impact

- Code: `scripts/benchmarking/lynx/backends.py`, `scripts/benchmarking/lynx/score.py`,
  `scripts/benchmarking/slurm/lynx_score.sbatch`.
- Docs: `docs/docs/benchmarking.md`.
- Tests: `tests/unit/test_lynx_backends.py`, `tests/unit/test_lynx_score.py`,
  `tests/unit/test_lynx_sbatch.py`, `tests/unit/test_benchmarking_docs_lynx.py`. No test
  needs a GPU, `vllm`, `transformers`, Slurm, Apptainer or the network.
- No new dependency. `scripts/benchmarking/lynx/` still imports only the standard library
  at module level.
- Out of scope: the first cluster run of Lynx 70B (#737), the offload judge job (#733),
  and showing the GPU count in the `compare_runs.py` report.
