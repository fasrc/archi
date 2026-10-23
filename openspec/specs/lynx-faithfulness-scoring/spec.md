# lynx-faithfulness-scoring Specification

## Purpose
A per-row PatronusAI Lynx faithfulness verdict for a `judge_mode: deferred` run, scored offline from the run's judge bundle. It covers the model-card prompt, the strict PASS or FAIL parser, the overflow skips, the output file outside the bundle, the descriptive Lynx block and paired exact test in `compare_runs.py`, the FASRC Slurm job that runs the scorer in a pinned vLLM container, and the docs that tell an operator how to run it and read it.
## Requirements
### Requirement: The Lynx prompt is the model-card template
The Lynx scorer SHALL build each prompt from the model-card template of `PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct`, byte for byte, with QUESTION = `user_input`, DOCUMENT = the `retrieved_contexts` joined with `"\n\n"`, and ANSWER = `response`. It SHALL NOT use `reference`. The vLLM backend SHALL send the prompt as one user chat message, and it SHALL count the tokens of that same chat text.

#### Scenario: Prompt equals the template
- **WHEN** a row has a question, two contexts and an answer
- **THEN** the prompt equals the fixture copy of the template with only the three fields filled, byte for byte

#### Scenario: Braces in a row
- **WHEN** a row's answer holds `{` and `}`
- **THEN** the prompt holds them unchanged, and the template's own JSON example keeps single braces

### Requirement: Only PASS and FAIL are verdicts
The Lynx parser SHALL return a verdict only when the `SCORE` of the JSON object in the model output is exactly `PASS` or `FAIL` after `strip()`. Any other output SHALL be a parse error, and a parse error SHALL NOT count as PASS or as FAIL.

#### Scenario: Valid verdicts
- **WHEN** the output is `{"REASONING": "...", "SCORE": "PASS"}` or the same with `"FAIL"`
- **THEN** the verdict is PASS or FAIL

#### Scenario: Parse errors
- **WHEN** the `SCORE` is `"pass."` or `"pass"`, the object has no `SCORE`, the output has no JSON object, or two objects have different scores
- **THEN** the row is a parse error with no verdict

### Requirement: A row that does not fit is skipped
The Lynx scorer SHALL mark a row `skipped_overflow` when its prompt tokens plus 600 is more than 8,000, it SHALL NOT send that row to the backend, and it SHALL NOT truncate any row.

#### Scenario: Large row
- **WHEN** a row's prompt is 7,401 tokens and another row's prompt is 7,400 tokens
- **THEN** the first row is `skipped_overflow`, the stub backend never gets it, and the second row is scored

### Requirement: The PASS rate divides by scored rows only
The Lynx output SHALL record `lynx_scored`, `lynx_pass`, `lynx_skipped_overflow` and `lynx_parse_errors`, and `lynx_faithfulness` SHALL be `lynx_pass / lynx_scored`, or `null` when `lynx_scored` is 0.

#### Scenario: Mixed rows
- **WHEN** a bundle has 2 PASS rows, 1 FAIL row, 1 overflow row and 1 parse-error row
- **THEN** the counts are 3 scored, 2 pass, 1 skipped and 1 parse error, and `lynx_faithfulness` is 2/3

#### Scenario: Nothing scored
- **WHEN** every row is skipped or a parse error
- **THEN** `lynx_faithfulness` is `null`

### Requirement: The scorer refuses an incomplete or corrupt bundle
The Lynx scorer SHALL exit non-zero and write no Lynx file when the bundle folder has neither `BUNDLE_COMPLETE` nor `READY`, when the SHA-256 of `rows.jsonl` differs from the manifest's `rows_sha256`, when the folder name is not `<result_basename>-arm<arm_index>`, or when a row is malformed or repeats a key. It SHALL do these checks before it builds the backend.

#### Scenario: No marker
- **WHEN** the bundle folder has `rows.jsonl` and `manifest.json` but no `BUNDLE_COMPLETE` and no `READY`
- **THEN** the scorer exits non-zero, writes no Lynx file, and builds no backend

#### Scenario: Digest mismatch
- **WHEN** one byte of `rows.jsonl` differs from the bytes the manifest's digest covers
- **THEN** the scorer exits non-zero and writes no Lynx file

#### Scenario: Queue copy
- **WHEN** the folder is a queue copy with `READY` and no `BUNDLE_COMPLETE`, and its digest matches
- **THEN** the scorer scores it

### Requirement: The scorer never changes the bundle
The Lynx scorer SHALL NOT create, change or remove any file in the bundle folder. It SHALL write `<out-dir>/<result_basename>-arm<arm_index>.lynx.json` only to a required `--out-dir` that is not the bundle folder and not inside it, and it SHALL NOT replace an existing Lynx file.

#### Scenario: Bundle unchanged
- **WHEN** the scorer scores a bundle
- **THEN** the names and SHA-256 digests of the files in the bundle folder are the same before and after

#### Scenario: Output inside the bundle
- **WHEN** `--out-dir` is the bundle folder
- **THEN** the scorer exits non-zero and writes nothing

#### Scenario: Lynx file exists
- **WHEN** the Lynx file already exists
- **THEN** the scorer exits non-zero and the file is unchanged

### Requirement: compare_runs shows the Lynx block only when a Lynx file exists
`compare_runs.py` SHALL look for `<basename>-arm<N-1>.lynx.json` beside the result JSON of arm `@N`, where `basename` is the file name without `.json` and an optional `.judged`. With no Lynx file it SHALL show no Lynx block. With a Lynx file that matches the arm, it SHALL show the PASS rate and the four counts. With a Lynx file that does not match the arm (`result_basename`, `arm_index`, a recorded bundle digest, or counts that differ from its rows), it SHALL show the block as refused with the reason, and SHALL NOT change the exit code.

#### Scenario: No Lynx file
- **WHEN** neither arm has a Lynx file
- **THEN** the report has no Lynx section and no Lynx key, and the exit code is as before

#### Scenario: Judged result
- **WHEN** the result is `<basename>.judged.json` and `<basename>-arm0.lynx.json` is beside it
- **THEN** arm `@1` shows that file's PASS rate and counts

#### Scenario: Wrong arm
- **WHEN** the Lynx file's `arm_index` differs from the arm
- **THEN** the arm's Lynx block is refused and names both values, and the exit code is 0

### Requirement: The Lynx paired test uses only rows that both arms scored
`compare_runs.py` SHALL add a Lynx entry for each arm against the baseline, when both have a matching Lynx file, that runs `paired_binary` over the questions that both arms scored, with `True` for PASS. It SHALL pair on question text, it SHALL report the excluded questions by reason, and the entry SHALL NOT be Holm-adjusted or feed any gate.

#### Scenario: Excluded rows
- **WHEN** a question is scored in the baseline and `skipped_overflow` in the arm
- **THEN** the question is not in the pairs, and it is listed under `arm skipped_overflow`

#### Scenario: Positional keys differ
- **WHEN** the same question is `question_3` in the baseline and `question_2` in the arm
- **THEN** the paired test pairs the two rows by their question text

### Requirement: The Lynx Slurm job scores one run bundle in a pinned container
`scripts/benchmarking/slurm/lynx_score.sbatch` SHALL run the Lynx scorer with `--backend vllm` over one run bundle with `BUNDLE_COMPLETE`, through `apptainer exec --nv` of a `vllm/vllm-openai` image at an exact tag, with the model id and revision set at the top of the script. It SHALL refuse, with a non-zero exit and before it starts a container, a missing bundle folder, a folder with no `BUNDLE_COMPLETE`, a folder name that is not `<basename>-arm<N>`, and a run with no out dir. The out dir SHALL be the second argument, or else the nearest folder above the bundle folder that holds `<basename>.json`. The script SHALL NOT name a requeue partition, and it SHALL ask Slurm not to requeue the job. With `--dry-run`, it SHALL print the command, exit 0, and start nothing.

#### Scenario: Dry run
- **WHEN** the operator runs `bash scripts/benchmarking/slurm/lynx_score.sbatch --dry-run <bundle folder>` for a bundle under `<out_dir>/judge_bundles/` with `<basename>.json` in `<out_dir>`
- **THEN** the script exits 0, prints the `apptainer exec` command with `--nv`, the pinned image, the scorer module, the model id, the revision, `--out-dir <out_dir>` and `--backend vllm`, and starts no container

#### Scenario: Incomplete bundle
- **WHEN** the bundle folder is missing, or has no `BUNDLE_COMPLETE`
- **THEN** the script exits non-zero and starts no container

#### Scenario: No requeue
- **WHEN** a reviewer reads the script
- **THEN** it names no requeue partition and it has `#SBATCH --no-requeue`

### Requirement: The benchmarking docs describe the Lynx job and its output
`docs/docs/benchmarking.md` SHALL have a "Lynx faithfulness (offline)" section that gives the job and the scorer invocation, says that `--out-dir` is the folder of the result JSON and is outside the bundle folder, names the four counts, and says that the PASS rate is over scored rows only and gates nothing.

#### Scenario: Docs section
- **WHEN** a reader opens the Lynx section
- **THEN** it holds the scorer invocation, the `--out-dir` rule, the four count names, and the statement that the PASS rate gates nothing

