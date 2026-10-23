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

- [ ] 5.1 `tests/unit/test_lynx_compare_block.py`: no Lynx file gives no Lynx key and no
      Lynx section; a matching file shows the rate and four counts, for the pending name
      and the judged name; a wrong basename, a wrong arm index, a recorded bundle digest
      that differs, and counts that differ from the rows are refused with the reason, and
      the exit code stays 0. Then `report.py` and the call sites in `compare_runs.py` (D8).

## 6. Lynx paired test (Plan step 8)

- [ ] 6.1 `tests/unit/test_lynx_compare_paired.py`: the entry pairs on question text when
      the positional keys differ, uses only questions that both arms scored, lists the
      excluded questions by reason, and is not Holm-adjusted; no entry when one side has
      no matching file. Then the paired entry in `report.py` and `compare_runs.py` (D9).

## 7. Acceptance checks

- [ ] 7.1 `tests/unit/test_lynx_acceptance.py`: `import scripts.benchmarking.lynx.score`
      works with `vllm` and `transformers` blocked; `src/utils/benchmark_schema.py` and the
      `METRICS` of `compare_runs.py` do not name Lynx (D10).
