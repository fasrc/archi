Rules for every task:
- Read `design.md` (D1-D10) first. Issue #734 is the work order; where this change and the
  issue differ, this change wins, because it obeys the merged bundle code.
- TDD: write the failing test first, run it and watch it FAIL, then write the minimum code.
- The gate `bash scripts/gate.sh` runs before each commit. Never use `--no-verify`.
- Put new tests in new files. No test needs a GPU, `vllm`, `transformers` or the network.

## 1. Prompt (issue Plan step 2)

- [x] 1.1 `tests/unit/test_lynx_prompt.py` and `tests/unit/fixtures/lynx_prompt_template.tmpl`:
      the filled prompt equals the fixture template with the three fields filled, byte for
      byte; contexts join with `"\n\n"`; braces in a row stay unchanged; `reference` is not
      used. Then `scripts/benchmarking/lynx/__init__.py` and `prompt.py` (D2).

## 2. Parser (Plan step 4)

- [x] 2.1 `tests/unit/test_lynx_parse.py`: PASS and FAIL parse to verdicts (also with
      spaces around them); `"pass."`, `"pass"`, `"PASS."`, a missing `SCORE`, a number, no
      JSON, and two objects with different scores are parse errors; two objects with the
      same score give that score. Then `parse.py` (D4).

## 3. Backends (Plan step 5)

- [x] 3.1 `tests/unit/test_lynx_backends.py`: `StubBackend` records the prompts it got and
      counts tokens with its counter; `VllmBackend` with a fake `vllm` module in
      `sys.modules` builds `LLM(model, revision)`, applies the chat template, calls
      `generate` with `SamplingParams(temperature=0, max_tokens=600)`, and counts the
      templated text; importing `backends` does not import `vllm`. Then `backends.py` (D1, D2).

## 4. Scorer CLI (Plan steps 3 and 6)

- [x] 4.1 `tests/unit/test_lynx_score.py`: a bundle written by `write_judge_bundle` scores
      with the stub backend; overflow rows are skipped and never reach the backend;
      `lynx_faithfulness` divides by `lynx_scored` only, and is `null` at 0; no marker,
      a digest mismatch, a wrong folder name, a malformed row and a repeated key are
      refused with a non-zero exit, no Lynx file and no backend; a `READY` queue copy
      scores; the bundle files are unchanged; `--out-dir` inside the bundle is refused; an
      existing Lynx file is not replaced; the output is canonical JSON. Then `score.py`
      (D3, D5, D6, D7).

## 5. compare_runs Lynx block (Plan step 7)

- [x] 5.1 `tests/unit/test_lynx_compare_block.py`: no Lynx file gives no Lynx key and no
      Lynx section; a matching file shows the rate and four counts, for the pending name
      and the judged name; a wrong basename, a wrong arm index, a recorded bundle digest
      that differs, and counts that differ from the rows are refused with the reason, and
      the exit code stays 0. Then `report.py` and the call sites in `compare_runs.py` (D8).

## 6. Lynx paired test (Plan step 8)

- [x] 6.1 `tests/unit/test_lynx_compare_paired.py`: the entry pairs on question text when
      the positional keys differ, uses only questions that both arms scored, lists the
      excluded questions by reason, and is not Holm-adjusted; no entry when one side has
      no matching file. Then the paired entry in `report.py` and `compare_runs.py` (D9).

## 7. Acceptance checks

- [x] 7.1 `tests/unit/test_lynx_acceptance.py`: `import scripts.benchmarking.lynx.score`
      works with `vllm` and `transformers` blocked; `src/utils/benchmark_schema.py` and the
      `METRICS` of `compare_runs.py` do not name Lynx (D10).

## 8. Slurm job (issue #735, Plan steps 1-4)

Issue #735 is the work order for groups 8 and 9. Where it and D11 differ, D11 wins.
No test needs Slurm, a GPU, Apptainer or the network: stub `apptainer`, `sbatch` and
`squeue` executables on `PATH` record each call.

- [x] 8.1 `tests/unit/test_lynx_sbatch.py`: `bash scripts/benchmarking/slurm/lynx_score.sbatch
      --dry-run <bundle folder>` exits 0 and prints one `apptainer exec` command with `--nv`,
      the pinned image, `python3 -m scripts.benchmarking.lynx.score <bundle folder>`, the
      model id, the revision, `--out-dir` and `--backend vllm`; no stub records a call and
      no scratch folder is made. Then `scripts/benchmarking/slurm/lynx_score.sbatch` (D11).
- [x] 8.2 Same file: a missing folder, a folder with no `BUNDLE_COMPLETE` (also a `READY`
      queue copy), a folder name that is not `<basename>-arm<N>`, an existing Lynx file and
      a usage error exit non-zero and start nothing (D11).
- [x] 8.3 Same file: the out dir is the second argument, or else the nearest folder above
      the bundle folder that holds `<basename>.json`, also for a nested `judge_bundle_dir`;
      with neither, and with an out dir that is the bundle folder, the script exits
      non-zero (D11).
- [x] 8.4 Same file: the script text names neither requeue partition and has
      `#SBATCH --no-requeue`; the image setting has an exact tag, and the tag check fails on
      `:latest` and on no tag; the active `#SBATCH` lines ask for one GPU on `gpu`, and the
      70B variant is commented out (D11).
- [x] 8.5 Same file: without `--dry-run`, a run outside a Slurm job, a submit folder that is
      not the repo root, and an unset lab are refused before `apptainer`; in a Slurm job the
      stub `apptainer` gets the same arguments that `--dry-run` prints (D11).

## 9. Docs (issue #735, Plan step 5)

- [x] 9.1 `tests/unit/test_benchmarking_docs_lynx.py`: the "Lynx faithfulness (offline)"
      section of `docs/docs/benchmarking.md` gives the job and the scorer invocation, says
      that `--out-dir` is the folder of the result JSON and is outside the bundle folder,
      names the four counts, says that the PASS rate is over scored rows only, and says
      that it gates nothing. Then the section (D12). `mkdocs build --strict` passes.
