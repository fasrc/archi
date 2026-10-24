## ADDED Requirements

### Requirement: The scorer splits the model over the given GPU count
The Lynx scorer SHALL take `--tensor-parallel-size N`, with 1 as the default, and the vLLM backend SHALL build `LLM` with `tensor_parallel_size=N`. The scorer SHALL refuse a value below 1 with exit 1, before it reads the bundle and before it builds the backend. The Lynx file SHALL record `tensor_parallel_size`.

#### Scenario: Two GPUs
- **WHEN** the scorer runs with `--tensor-parallel-size 2`
- **THEN** the backend factory gets `tensor_parallel_size=2`, the vLLM backend calls `LLM` with `tensor_parallel_size=2`, and the Lynx file records `"tensor_parallel_size": 2`

#### Scenario: Default
- **WHEN** the scorer runs with no `--tensor-parallel-size`
- **THEN** the vLLM backend calls `LLM` with `tensor_parallel_size=1`, and the Lynx file records `"tensor_parallel_size": 1`

#### Scenario: Below 1
- **WHEN** the scorer runs with `--tensor-parallel-size 0` or `--tensor-parallel-size -1`
- **THEN** the scorer exits 1, builds no backend and writes no Lynx file

## MODIFIED Requirements

### Requirement: The Lynx Slurm job scores one run bundle in a pinned container
`scripts/benchmarking/slurm/lynx_score.sbatch` SHALL run the Lynx scorer with `--backend vllm` over one run bundle with `BUNDLE_COMPLETE`, through `apptainer exec --nv` of a `vllm/vllm-openai` image at an exact tag, with the model id, the revision and the GPU count (`GPUS`) set at the top of the script. It SHALL pass `GPUS` to the scorer as `--tensor-parallel-size`, and the active `#SBATCH --gres=gpu:<N>` SHALL ask for `GPUS` GPUs. Its commented Lynx 70B variant SHALL set `GPUS` to 2 and ask for two GPUs. It SHALL refuse, with a non-zero exit and before it starts a container, a missing bundle folder, a folder with no `BUNDLE_COMPLETE`, a folder name that is not `<basename>-arm<N>`, and a run with no out dir. The out dir SHALL be the second argument, or else the nearest folder above the bundle folder that holds `<basename>.json`. The script SHALL NOT name a requeue partition, and it SHALL ask Slurm not to requeue the job. With `--dry-run`, it SHALL print the command, exit 0, and start nothing.

#### Scenario: Dry run
- **WHEN** the operator runs `bash scripts/benchmarking/slurm/lynx_score.sbatch --dry-run <bundle folder>` for a bundle under `<out_dir>/judge_bundles/` with `<basename>.json` in `<out_dir>`
- **THEN** the script exits 0, prints the `apptainer exec` command with `--nv`, the pinned image, the scorer module, the model id, the revision, `--out-dir <out_dir>`, `--backend vllm` and `--tensor-parallel-size 1`, and starts no container

#### Scenario: Lynx 70B variant
- **WHEN** the operator puts the commented 70B `##SBATCH` lines and settings in place of the active ones and runs the dry run
- **THEN** the command names the 70B model id and revision and `--tensor-parallel-size 2`, and the `--gres` line asks for two GPUs

#### Scenario: Incomplete bundle
- **WHEN** the bundle folder is missing, or has no `BUNDLE_COMPLETE`
- **THEN** the script exits non-zero and starts no container

#### Scenario: No requeue
- **WHEN** a reviewer reads the script
- **THEN** it names no requeue partition and it has `#SBATCH --no-requeue`

### Requirement: The benchmarking docs describe the Lynx job and its output
`docs/docs/benchmarking.md` SHALL have a "Lynx faithfulness (offline)" section that gives the job and the scorer invocation, says that `--out-dir` is the folder of the result JSON and is outside the bundle folder, names the four counts, and says that the PASS rate is over scored rows only and gates nothing. It SHALL give the Lynx 70B settings of the job, including `GPUS`, and the scorer's `--tensor-parallel-size` option.

#### Scenario: Docs section
- **WHEN** a reader opens the Lynx section
- **THEN** it holds the scorer invocation, the `--out-dir` rule, the four count names, and the statement that the PASS rate gates nothing

#### Scenario: Lynx 70B
- **WHEN** a reader wants to score with Lynx 70B
- **THEN** the section names the 70B model id, the lines to swap in the job, `GPUS`, and `--tensor-parallel-size`
