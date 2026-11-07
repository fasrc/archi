## ADDED Requirements

### Requirement: The scorer takes the token limit as a setting
The Lynx scorer SHALL take `--context-tokens N`, with 8,000 as the default, and the vLLM backend SHALL build `LLM` with `max_model_len=N`. The scorer SHALL refuse an `N` of 600 or less with exit 1, before it reads the bundle and before it builds the backend. The Lynx file SHALL record `N` as `token_budget.context_tokens`.

#### Scenario: Default
- **WHEN** the scorer runs with no `--context-tokens`
- **THEN** the backend factory gets `context_tokens=8000`, the vLLM backend calls `LLM` with `max_model_len=8000`, and the Lynx file records `"context_tokens": 8000`

#### Scenario: A larger limit
- **WHEN** the scorer runs with `--context-tokens 32000`
- **THEN** the backend factory gets `context_tokens=32000`, the vLLM backend calls `LLM` with `max_model_len=32000`, and the Lynx file records `"context_tokens": 32000`

#### Scenario: Too small
- **WHEN** the scorer runs with `--context-tokens 600`, `--context-tokens 0` or `--context-tokens -1`
- **THEN** the scorer exits 1, builds no backend and writes no Lynx file

## MODIFIED Requirements

### Requirement: A row that does not fit is skipped
The Lynx scorer SHALL mark a row `skipped_overflow` when its prompt tokens plus 600 is more than the token limit (`--context-tokens`, 8,000 by default), it SHALL NOT send that row to the backend, and it SHALL NOT truncate any row.

#### Scenario: Large row
- **WHEN** a row's prompt is 7,401 tokens and another row's prompt is 7,400 tokens, with the default limit
- **THEN** the first row is `skipped_overflow`, the stub backend never gets it, and the second row is scored

#### Scenario: Large row under a larger limit
- **WHEN** a row's prompt is 15,401 tokens and another row's prompt is 15,400 tokens, with `--context-tokens 16000`
- **THEN** the first row is `skipped_overflow`, the stub backend never gets it, and the second row is scored

### Requirement: compare_runs shows the Lynx block only when a Lynx file exists
`compare_runs.py` SHALL look for `<basename>-arm<N-1>.lynx.json` beside the result JSON of arm `@N`, where `basename` is the file name without `.json` and an optional `.judged`. With no Lynx file it SHALL show no Lynx block. With a Lynx file that matches the arm, it SHALL show the PASS rate, the four counts and the token limit the file records. With a Lynx file that does not match the arm (`result_basename`, `arm_index`, a recorded bundle digest, or counts that differ from its rows), it SHALL show the block as refused with the reason, and SHALL NOT change the exit code.

#### Scenario: No Lynx file
- **WHEN** neither arm has a Lynx file
- **THEN** the report has no Lynx section and no Lynx key, and the exit code is as before

#### Scenario: Judged result
- **WHEN** the result is `<basename>.judged.json` and `<basename>-arm0.lynx.json` is beside it
- **THEN** arm `@1` shows that file's PASS rate and counts

#### Scenario: Wrong arm
- **WHEN** the Lynx file's `arm_index` differs from the arm
- **THEN** the arm's Lynx block is refused and names both values, and the exit code is 0

#### Scenario: Token limit
- **WHEN** the baseline's Lynx file records `"context_tokens": 8000` and the arm's records `"context_tokens": 32000`
- **THEN** the block shows 8000 for the baseline and 32000 for the arm, and the section's prose names no fixed limit

### Requirement: The Lynx Slurm job scores one run bundle in a pinned container
`scripts/benchmarking/slurm/lynx_score.sbatch` SHALL run the Lynx scorer with `--backend vllm` over one run bundle with `BUNDLE_COMPLETE`, through `apptainer exec --nv` of a `vllm/vllm-openai` image at an exact tag, with the model id, the revision, the GPU count (`GPUS`) and the token limit (`CONTEXT_TOKENS`) set at the top of the script. It SHALL pass `GPUS` to the scorer as `--tensor-parallel-size` and `CONTEXT_TOKENS` as `--context-tokens`, and the active `#SBATCH --gres=gpu:<N>` SHALL ask for `GPUS` GPUs. The active settings SHALL be Lynx 8B at its pinned revision with `CONTEXT_TOKENS="8000"`. Its commented Lynx 70B variant SHALL set `GPUS` to 2 and ask for two GPUs. Its commented Lynx 8B v1.1 variant SHALL set `MODEL_ID` to `PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct-v1.1`, `MODEL_REVISION` to `00de9f9062d93a95e4e359d3c92b8ea1d0114738` and `CONTEXT_TOKENS` to `"32000"`, on one GPU. It SHALL refuse, with a non-zero exit and before it starts a container, a `GPUS` that is not a whole number of 1 or more, a `CONTEXT_TOKENS` that is not a whole number above 600, a missing bundle folder, a folder with no `BUNDLE_COMPLETE`, a folder name that is not `<basename>-arm<N>`, and a run with no out dir. The out dir SHALL be the second argument, or else the nearest folder above the bundle folder that holds `<basename>.json`. The script SHALL NOT name a requeue partition, and it SHALL ask Slurm not to requeue the job. With `--dry-run`, it SHALL print the command, exit 0, and start nothing.

#### Scenario: Dry run
- **WHEN** the operator runs `bash scripts/benchmarking/slurm/lynx_score.sbatch --dry-run <bundle folder>` for a bundle under `<out_dir>/judge_bundles/` with `<basename>.json` in `<out_dir>`
- **THEN** the script exits 0, prints the `apptainer exec` command with `--nv`, the pinned image, the scorer module, the model id, the revision, `--out-dir <out_dir>`, `--backend vllm`, `--tensor-parallel-size 1` and `--context-tokens 8000`, and starts no container

#### Scenario: Lynx 70B variant
- **WHEN** the operator puts the commented 70B `##SBATCH` lines and settings in place of the active ones and runs the dry run
- **THEN** the command names the 70B model id and revision and `--tensor-parallel-size 2`, and the `--gres` line asks for two GPUs

#### Scenario: Lynx 8B v1.1 variant
- **WHEN** the operator puts the commented v1.1 settings in place of the active ones and runs the dry run
- **THEN** the command names `PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct-v1.1`, revision `00de9f9062d93a95e4e359d3c92b8ea1d0114738`, `--tensor-parallel-size 1` and `--context-tokens 32000`

#### Scenario: Bad token limit
- **WHEN** `CONTEXT_TOKENS` is `"600"`, `"abc"` or `""`
- **THEN** the script exits non-zero, names `CONTEXT_TOKENS`, and starts no container

#### Scenario: Incomplete bundle
- **WHEN** the bundle folder is missing, or has no `BUNDLE_COMPLETE`
- **THEN** the script exits non-zero and starts no container

#### Scenario: No requeue
- **WHEN** a reviewer reads the script
- **THEN** it names no requeue partition and it has `#SBATCH --no-requeue`

### Requirement: The benchmarking docs describe the Lynx job and its output
`docs/docs/benchmarking.md` SHALL have a "Lynx faithfulness (offline)" section that gives the job and the scorer invocation, says that `--out-dir` is the folder of the result JSON and is outside the bundle folder, names the four counts, and says that the PASS rate is over scored rows only and gates nothing. It SHALL give the Lynx 70B settings of the job, including `GPUS`, and the scorer's `--tensor-parallel-size` option. It SHALL give the scorer's `--context-tokens` option and its default, the job's `CONTEXT_TOKENS` setting, the Lynx 8B v1.1 settings of the job, and that vLLM refuses a limit above the model's trained length. It SHALL show the output form that Lynx writes and say which outputs are parse errors.

#### Scenario: Docs section
- **WHEN** a reader opens the Lynx section
- **THEN** it holds the scorer invocation, the `--out-dir` rule, the four count names, and the statement that the PASS rate gates nothing

#### Scenario: Lynx 70B
- **WHEN** a reader wants to score with Lynx 70B
- **THEN** the section names the 70B model id, the lines to swap in the job, `GPUS`, and `--tensor-parallel-size`

#### Scenario: Lynx 8B v1.1
- **WHEN** a reader wants to score long rows with Lynx 8B v1.1
- **THEN** the section names the v1.1 model id and revision, `CONTEXT_TOKENS="32000"`, `--context-tokens` with its default of 8,000, and that vLLM refuses a limit above the model's trained length

#### Scenario: Output form
- **WHEN** a reader wants to know why a row is a parse error
- **THEN** the section shows Lynx's form (single-quoted reasoning items and a bare `PASS` or `FAIL`) and lists the outputs that are parse errors
