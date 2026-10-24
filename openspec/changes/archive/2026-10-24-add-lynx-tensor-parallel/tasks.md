Rules for every task:
- Read `design.md` (D1-D4) first. Issue #755 is the work order.
- TDD: write the failing test first, run it and watch it FAIL, then write the minimum code.
- The gate `bash scripts/gate.sh` runs before each commit. Never use `--no-verify`.
- No test needs a GPU, `vllm`, `transformers`, Slurm, Apptainer or the network.

## 1. Backend (issue Plan steps 1-2)

- [x] 1.1 `tests/unit/test_lynx_backends.py`: `VllmBackend(model, revision,
      tensor_parallel_size=2)` calls the fake `LLM` with `tensor_parallel_size=2`, the
      default is 1, and `make_backend("vllm", ..., tensor_parallel_size=2)` passes it on.
      Then `backends.py` (D1).

## 2. Scorer CLI (Plan steps 3-4)

- [x] 2.1 `tests/unit/test_lynx_score.py`: `--tensor-parallel-size 2` reaches the backend
      factory; `0` and `-1` exit 1 with no backend built and no Lynx file; the Lynx file
      records `tensor_parallel_size` (1 by default, 2 when given). Then `score.py` (D2, D3).

## 3. Slurm job (Plan step 5)

- [x] 3.1 `tests/unit/test_lynx_sbatch.py`: the dry-run command ends with
      `--tensor-parallel-size` and the `GPUS` setting; `GPUS` is 1 and equals the active
      `--gres=gpu:<N>`; after the 70B swap the dry run names the 70B model and
      `--tensor-parallel-size 2`, and `--gres=gpu:2` equals `GPUS`; the NOTE that the 70B
      variant cannot load the model is gone; a `GPUS` of `0`, `""`, `two` or `1.5` is
      refused before the container starts. Then `lynx_score.sbatch` (D4).

## 4. Docs (Plan step 6)

- [x] 4.1 `tests/unit/test_benchmarking_docs_lynx.py`: the Lynx section names
      `--tensor-parallel-size`, `GPUS` and the 70B model id. Then the "Lynx faithfulness
      (offline)" section of `docs/docs/benchmarking.md`.

## 5. Gate

- [x] 5.1 `python -m pytest tests/unit -k lynx -q` passes, and `bash scripts/gate.sh`
      exits 0.
