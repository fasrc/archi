## Context

Issue #734 is the work order. Its Plan steps 1-8 and its Constraints hold, with the four
corrections that `proposal.md` lists, which come from the merged bundle code of #731
(`c19f889e`). Anchors in this file are against `origin/dev` at `c19f889e`.

The bundle writer is `write_judge_bundle` (`src/utils/judge_bundle.py:203`). A bundle folder
holds `rows.jsonl` (one canonical JSON line per scorable row: `key`, `user_input`,
`retrieved_contexts`, `response`, `reference`), `manifest.json` (`bundle_format`,
`result_basename`, `arm_index`, `arm_count`, `row_count`, `rows_sha256` as
`sha256:<hex>`, and more), and an empty `BUNDLE_COMPLETE`, written last. The pending
result records `judge_bundle: {folder, digest}` for each deferred arm
(`pending_arm_fields`, `src/utils/judge_bundle.py:285`).

## Goals / Non-Goals

**Goals:** a per-row Lynx verdict with a strict parser, overflow skips, a canonical output
file outside the bundle, and a descriptive Lynx block plus a paired exact test in
`compare_runs.py`.

Issue #735 adds the Slurm job and the docs to this change (D11, D12). Its anchors are
against `origin/dev` at `6282a299`, where the scorer of #734 is merged.

**Non-Goals:** any gate, any change to the bundle writer or the publish step, any use of
Lynx as the RAGAS judge, a run on the cluster (an operator step of #737), and Lynx 70B on
more than one GPU (#755).

## Decisions

### D1. A package of small modules, standard library at module level

`scripts/benchmarking/lynx/` holds `prompt.py`, `parse.py`, `backends.py`, `score.py` and
`report.py`. No module imports `vllm`, `transformers` or any other package outside the
standard library at module level, so `python -c "import scripts.benchmarking.lynx.score"`
works in the gate env. `VllmBackend.__init__` imports `vllm`. Tests use `StubBackend`, and
the `VllmBackend` tests put a fake `vllm` module in `sys.modules`.

### D2. The prompt is the model-card template, sent as one user chat message

`prompt.py` holds the `PROMPT` string of the model card
(`PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct`, README at revision
`5523f869eb5eecb4bca417b6e6d3532e746a9664`), with its first and last newline, and fills it
with `str.format`. A test compares the filled prompt with a fixture copy of the template
(`tests/unit/fixtures/lynx_prompt_template.tmpl`), byte for byte. `reference` is not used.

The model card says the model is trained in chat format and passes the prompt as one
`user` message. So `VllmBackend` applies the tokenizer's chat template
(`apply_chat_template(..., add_generation_prompt=True, tokenize=False)`) and gives the
result to `LLM.generate` with `SamplingParams(temperature=0, max_tokens=600)`. Its
`count_tokens` counts the same templated text, so the token budget counts what the model
gets.

### D3. A row that does not fit is skipped, never truncated

For each row, `score.py` counts the prompt tokens with the backend. When
`prompt_tokens + 600 > 8000`, the row is `skipped_overflow`, and the backend never gets it.
No text is cut.

### D4. A strict parser

`parse.py` decodes every top-level JSON object in the output (`json.JSONDecoder.raw_decode`
at each `{` that is not inside an object it already decoded). The verdict comes from the
`SCORE` values of these objects. Only the exact strings `PASS` and `FAIL`, after `strip()`,
are verdicts. These are parse errors: no JSON object, no `SCORE` key, a `SCORE` that is not
one of the two strings (`"pass"`, `"PASS."`, a number), two objects with different scores,
`NaN`, `Infinity` or a number too large for a float (`1e309`) in an object, and a top-level `{` that does not start a complete JSON
object. The last rule stops a cut-off answer, such as
`{"REASONING": {"SCORE": "PASS"}, "SCORE": "FAIL"`, from giving the verdict of a nested
object; a stray `{` in prose before the answer is also a parse error. A parse error gives
`verdict: null` and `status: parse_error`, and it is in neither the numerator nor the
denominator.

### D5. Bundle checks before any model call

`score.py` refuses, with a non-zero exit and no output file, a bundle folder that:

- has neither `BUNDLE_COMPLETE` (the run's own bundle) nor `READY` (the queue copy, which
  `publish.py` writes last, after its digest check, `publish.py:131-140`);
- has a `manifest.json` that does not load, or a `rows.jsonl` whose SHA-256 differs from
  `rows_sha256`;
- has a folder name other than `<result_basename>-arm<arm_index>` (the check of
  `publish.py:97`);
- has a row that is not an object with a string `key`, a string `user_input`, a string
  `response` and a list of strings `retrieved_contexts`, or two rows with one key, or a
  line count other than `row_count`.

These checks run before the backend is built, so a refusal loads no model.

### D6. The output goes to a required `--out-dir`, outside the bundle

The issue says to write `<out_dir>/<basename>-arm<N>.lynx.json` beside the bundle folder,
and `compare_runs.py` looks for it beside the result JSON. With the merged layout these are
two folders (`<out>/judge_bundles/` and `<out>/`; in the queue, `<queue>/` and
`<queue>/results/`), and the bundle folder does not record where its result JSON is. No
default is right for both layouts, so `--out-dir` is required, and the operator gives the
folder of the result JSON. `score.py` refuses an `--out-dir` that is the bundle folder or
inside it.

The file is created and never replaced: `score.py` writes a temporary file in `--out-dir`
and publishes it with a no-replace `os.link`. An existing Lynx file makes the run exit
non-zero and leaves the file unchanged. To score again, the operator removes the old file.

### D7. The output file

Canonical JSON (`sort_keys=True`, `allow_nan=False`, two-space indent, a final newline):

```
{model_id, model_revision, backend, result_basename, arm_index, bundle_rows_sha256,
 token_budget: {context_tokens: 8000, max_new_tokens: 600},
 rows: {<key>: {verdict, status, reasoning, prompt_tokens[, error, output]}},
 counts: {lynx_scored, lynx_pass, lynx_skipped_overflow, lynx_parse_errors},
 lynx_faithfulness}
```

`status` is `scored`, `skipped_overflow` or `parse_error`. `reasoning` is the `REASONING`
value of the object that gave the verdict, or `null`. A `parse_error` row also has `error`
(the reason) and `output` (the raw model text), so a person can audit it. `lynx_scored` is
the number of PASS and FAIL rows, and `lynx_faithfulness` is `lynx_pass / lynx_scored`, or
`null` when `lynx_scored` is 0.

### D8. `compare_runs.py` finds the file beside the result JSON

For the arm `path@N`, the file is `<parent of path>/<basename>-arm<N-1>.lynx.json`, where
`basename` is the file name without `.json` and then without an optional `.judged`. So the
pending result and the judged result find the same file. No file: no Lynx block for that
arm, and no change to the report. The new logic is in `scripts/benchmarking/lynx/report.py`;
`compare_runs.py` has only thin call sites.

The file is refused, with the reason in the block, when it does not load as strict JSON (no `NaN`, `Infinity` or float overflow, which would crash the report's `allow_nan=False` write), when its
`result_basename` or `arm_index` differs from the arm, when the arm records
`judge_bundle.digest` and the file's `bundle_rows_sha256` differs, when a row has an
unknown status or a verdict that does not agree with its status, or when the recorded
counts differ from the counts of its rows. A refusal does not change the exit code.

### D9. The paired test pairs on question text

Bundle keys are the arm's positional `question_<n>` keys. `report.py` maps each key to the
question text through the arm's `single_question_results`, and pairs on that text, as gate
G5 does. `paired_binary` runs over the questions that both arms scored (`status ==
"scored"`), with `True` for PASS. The questions are those that have a Lynx row in either
file. Each excluded question is listed under each reason that applies:
`baseline skipped_overflow`, `baseline parse_error`, `baseline no Lynx row`, and the same
three for `arm`. A Lynx key that names no row of its result is listed, as a key, under
`baseline key not in result` or `arm key not in result`.

The entry is in the Lynx section of the report (`report["lynx"]["paired"]`), not in the
`paired_tests` table, so the Holm family and the pre-registered primaries stay unchanged.
It has no Holm adjustment and no primary role, and it feeds no gate. There is no entry for
an arm when the arm's or the baseline's Lynx file is missing or refused.

### D10. Not a RAGAS metric, not a gate

Lynx is not added to `RAGAS_METRIC_NAMES`, `build_ragas_metric_objects`, the `METRICS` of
`compare_runs.py`, `ResultHandler.LEADERBOARD_METRICS`, the Holm set, or any gate.

### D11. The Slurm job scores one run bundle in a pinned vLLM container

`scripts/benchmarking/slurm/lynx_score.sbatch [--dry-run] <bundle folder> [<out dir>]` is
its own job. It uses none of the queue, claims or merge of the offload judge job (#733).
The settings are at the top of the file: the image, the model id, the model revision, and
the `#SBATCH` partition, GPU count and time limit.

- **Input.** The job scores a run's own bundle, `<out_dir>/<judge_bundle_dir>/<basename>-arm<N>/`
  with `BUNDLE_COMPLETE`. It refuses a missing folder, a folder with no `BUNDLE_COMPLETE`
  (a `READY` queue copy too, because the job does not use the queue), and a folder name
  that is not `<basename>-arm<N>`. The scorer does the full checks of D5 again in the job.
- **Out dir.** The second argument, or else the nearest folder above the bundle folder that
  holds `<basename>.json`, the result JSON of the run (`publish.py` reads it there). D6
  says that no default fits both bundle layouts. This default is not a guess about the
  layout: it finds the result file itself, so a nested `judge_bundle_dir` works too. With
  no such folder and no second argument, the job exits non-zero.
- **Partition.** `-p gpu` with one GPU, not `gpu_test` as the issue says. On `gpu_test` a
  GPU is a 3g.20GB MIG slice of an A100 (FASRC "Running Jobs" page). The Lynx 8B weights
  are 16,060,556,096 bytes (Hugging Face `usedStorage` at the pinned revision). vLLM uses
  90% of a GPU by default. On a 19,968 MiB slice, that leaves about 2.8 GB for the
  activations, the CUDA graphs and the KV cache together, and one 8,192-token sequence
  needs 1 GiB of KV cache (32 layers x 2 x 8 KV heads x 128 x 2 bytes per token). The job
  can fail at load, and the scorer exposes no memory setting. `gpu` gives a full A100 and
  is not preempted. The script names no requeue partition and
  sets `--no-requeue`, because the scorer cannot resume a half-scored bundle.
- **Image.** `docker://vllm/vllm-openai:v0.31.0-cu129`, an exact tag. The plain `v0.31.0`
  image is built on CUDA 13.0.2, which needs a newer NVIDIA driver than the CUDA 12.9
  build. The image entrypoint is `vllm serve`; `apptainer exec` does not run it, and the
  image's Python is `python3`.
- **Command.** `apptainer exec --nv --bind <repo>,<bundle>,<out dir>,<scratch> --pwd <repo>
  --env HF_HOME=<scratch>/hf --env PYTHONNOUSERSITE=1 <image> python3 -m
  scripts.benchmarking.lynx.score <bundle> --model <id> --revision <sha> --out-dir <out dir>
  --backend vllm`. Apptainer binds `$HOME` by default, so `PYTHONNOUSERSITE=1` keeps the
  user's `~/.local` packages out of the container's Python. No `--cleanenv`: Slurm's
  `CUDA_VISIBLE_DEVICES` must reach vLLM.
- **Scratch.** `HF_HOME` and `APPTAINER_CACHEDIR` are under
  `${SCRATCH:-/n/netscratch}/<lab>/Lab/$USER/archi-lynx` (FASRC sets `$SCRATCH`), with the
  lab from `LYNX_LAB`, or the whole folder from `LYNX_SCRATCH`. The home folder is too
  small for a 16 GB model and a 10 GB image.
- **Repo.** In a job, the repo is `$SLURM_SUBMIT_DIR`, because Slurm runs a copy of the
  script from its spool folder; the job must be submitted from the repo root. With
  `--dry-run`, the repo is the folder three levels above the script.
- **Guards.** `--dry-run` prints the command and exits 0. It makes no folder and starts
  nothing. Without `--dry-run`, the script refuses to run outside a Slurm job
  (`SLURM_JOB_ID` unset), from a submit folder with no `scripts/benchmarking/lynx/score.py`,
  or with no lab and no `LYNX_SCRATCH`.
- **70B.** A commented variant (`gpu_h200`, two GPUs, the 70B id and revision) is in the
  file with a NOTE: `VllmBackend` passes no `tensor_parallel_size` (`backends.py:57`), so
  the variant cannot load the 141 GB model until #755 is done.

### D12. The docs

`docs/docs/benchmarking.md` gets a "Lynx faithfulness (offline)" section after the "Results"
section. It is not in the deferred judge section, which the offload work (#732, #733) edits.
It says what Lynx measures; that it scores only `judge_mode: deferred` runs;
how to submit the job and how to run the scorer by hand; that `--out-dir` is the folder of
the result JSON, outside the bundle folder, so that `compare_runs.py` finds the file; how to
read `<basename>-arm<N>.lynx.json` and the four counts; and that the PASS rate is over scored
rows only, is descriptive, and gates nothing. A unit test keeps these statements in the
section.

## Risks / Trade-offs

- A Llama 3 chat template from a newer `transformers` can differ from the one in training.
  The model revision pins the tokenizer files, and the vLLM image (#735) pins the code.
- A Lynx file from another run with the same basename is refused only when the arm records
  a bundle digest. A deferred arm always records one (`pending_arm_fields`).
- The 8,000-token limit skips large rows, so the PASS rate covers only the rows that fit.
  The block shows the skip count beside the rate.
- No test runs the job on the cluster. The first Lynx 8B run is an operator step (#737,
  step 7). That run is the first proof of the image, the driver and the memory settings.
- The first job converts the image into the Apptainer cache, which uses part of the time
  limit. Later jobs use the cached image.
