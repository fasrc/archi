## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-582-eval-token-usage` exists, cut from `origin/dev` at
      `e58a7ada`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-582-eval-token-usage --strict` passed on the host. The
      `openspec` CLI is **not usable in this container** — do not run it, and do not add a
      task that does. It is already green.
- [x] 0.3 The gap is measured at `e58a7ada`:
      `grep -rn usage_metadata src/evaluation/qa/ src/bin/service_benchmark.py` prints
      nothing. `src/utils/llm_usage.py` does not exist.
- [x] 0.4 Every design decision is made in `design.md` (D1–D8): our own handler (not
      langchain's `UsageMetadataCallbackHandler`, which drops HUIT messages), what counts as
      reported, the one `usage` shape, `last_usage` on the evaluator runtime, which rows get
      the key, phase totals, benchmark `judge_usage`, test seams. Follow the design; where the
      issue body and the design differ, the design wins.

## Rules that apply to EVERY task below

- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed and the loop halts there.
  Write the failing test, watch it fail, make it pass, then commit — all in one task.
- **Every task except 8.1 ends with exactly one commit** (tick its checkbox in this file in
  the same commit). Do not add a task that changes no file.
- Do not implement a later task's behaviour early. Each task's red must still fail when that
  task starts.
- Tokens only. No price, rate, currency or dollar value in any code, test or doc string
  except the docs arithmetic in task 7 (which uses a symbol like `rate_in`, no number).
- Additive schema only: never rename or remove an existing key in any artifact.
- Before you add a test, `grep -n 'def test_' <file>` and pick a name that is not already
  used. No linter runs, so a duplicate `def test_...` silently replaces the older test.
- Never append a test at the very end of an existing file without checking the diff: after
  each insert, `git diff` the file and confirm the test above yours still ends with its own
  `assert`.
- Never delete a test. After each commit,
  `git diff origin/dev -- tests/ | grep -c '^-.*def test_'` must print `0`.
- If a new keyword argument breaks an existing test fake (for example the fakes in
  `tests/unit/evaluation/qa/test_runtime.py:79` and `:100` define `invoke(self, messages)`),
  widen the fake's signature (`invoke(self, messages, config=None)`). Do not change that
  test's assertions.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. After each
  commit, `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer. Commit
  messages: short, lowercase.
- Do not edit `src/archi/providers/**`, `scripts/benchmarking/compare_runs.py`, or any
  control-plane file. Do not write under `docs/` except the one edit in task 7. The PR body
  goes in `/tmp`, never in the repo. Do not add an entry to `docs/questions.md`.

## 1. The usage helper

- [x] 1.1 Create `tests/unit/test_llm_usage.py` and `src/utils/llm_usage.py` (design D1–D3).
      Tests first, then code, one commit (`feat: llm usage recorder helper`). Tests build
      `LLMResult(generations=[[ChatGeneration(message=AIMessage(...))]])` by hand and call
      `UsageRecorder.on_llm_end` directly. Cover:
      (a) one message with `usage_metadata` 120/30 and no `response_metadata` →
      `snapshot()` is the D3 shape with `input_tokens` 120, `output_tokens` 30, `calls` 1,
      `unreported_calls` 0, one `by_model` entry with the constructor's provider and model;
      (b) `response_metadata["model_name"]` overrides the constructor model, and
      `response_metadata["model"]` is the second choice;
      (c) two generations in one `LLMResult` and two `on_llm_end` calls both sum;
      (d) two models → two `by_model` entries sorted by `(provider, model)`, top level = sums;
      (e) no usage anywhere → `calls` 1, `unreported_calls` 1, tokens 0;
      (f) `response_metadata["token_usage"]` with `prompt_tokens`/`completion_tokens` is read,
      and `response_metadata["usage"]` with `input_tokens`/`output_tokens` is read;
      (g) a `bool`, a negative int, a float or a string as a count → the call is unreported;
      (h) `snapshot()` is `None` before any `on_llm_end`;
      (i) `sum_usage([])` and `sum_usage([None, None])` are `None`; `sum_usage` merges
      `by_model` on `(provider, model)` and skips `None`; a non-dict input raises
      `ValueError`;
      (j) `phase_usage_totals(prep_rows, answer_rows, result_rows)` (design D6) returns
      `{"prepare", "run", "score"}` sums and `None` for a phase whose rows have no `usage`;
      (k) the snapshot has no `total_tokens` key.

## 2. QA evaluator runtime records `last_usage`

- [x] 2.1 In `tests/unit/evaluation/qa/test_runtime.py`, add tests, then implement design D4
      in `src/evaluation/qa/runtime.py`, one commit (`feat: qa evaluator records token usage`).
      Use a fake `model_factory` whose model's `with_structured_output(schema).invoke(messages,
      config=None)` calls `on_llm_end` on each callback in `config["callbacks"]` with an
      `AIMessage` carrying usage, then returns a valid dict. Assert: after `extract_gold`,
      `runtime.last_usage["by_model"][0]` has the `atoms_extractor` descriptor's provider and
      model; after `compare`, the `evaluator` descriptor's; a second call does not add to the
      first (reset per call); a fake that reports usage and then returns a non-dict still
      leaves `last_usage` set when `_structured` raises. Widen the two existing
      `invoke(self, messages)` fakes as the rules say.

## 3. QA preparation rows

- [x] 3.1 In `tests/unit/evaluation/qa/test_preparation.py`, add tests, then implement design
      D5 (preparation part) in `src/evaluation/qa/preparation.py`, one commit
      (`feat: record extractor usage on preparation rows`). Assert: an inferred-atoms item with
      an extractor fake exposing `last_usage` writes that dict as `usage` in `to_dict()`; an
      extractor that raises after setting `last_usage` gives a `preparation_failed` row with
      `usage`; a supplied-atoms item's row has no `usage` key and equals today's row; a fake
      without a `last_usage` attribute gives no `usage` key; `_record_from_row` round-trips a
      row with `usage` (prepared and failed) and still loads a row without it; a skipped
      record with `usage` set raises in `__post_init__`; `usage` that is not a dict or null
      raises on load.

## 4. QA answer and judgment rows

- [x] 4.1 In `tests/unit/evaluation/qa/test_phases.py` and `test_runtime.py`, add tests, then
      implement design D5 (run and score parts) in `src/evaluation/qa/runtime.py`
      (`ArchiAgentRuntime`) and `src/evaluation/qa/phases.py`, one commit
      (`feat: record agent and judge usage on qa rows`). Assert: `ArchiAgentRuntime.run` with a
      fake `pipeline_class` whose `invoke(**kwargs)` calls `on_llm_end` on every callback that
      has it sets `runtime.usage` with the chat `default_provider`/`default_model`, and sets it
      also when `invoke` raises after reporting; `run_attempt` writes `usage` on
      `answer_ready` and `execution_failed` rows and writes `"usage": None` for a runtime
      without the attribute; `score_answer` writes the evaluator's `last_usage` on `scored`
      and `evaluation_failed` rows; the `execution_failed` pass-through row from
      `score_attempts` has no `usage` key.

## 5. Phase totals in summary.json

- [x] 5.1 In `tests/unit/evaluation/qa/test_workflow.py`, add a test that runs the existing
      end-to-end fake workflow (find the fixture the other score tests use) with fakes that
      report usage, then read `summary.json`: `provenance.usage.prepare/run/score` equal the
      sums of the rows in `preparation.jsonl`, `answers.jsonl`, `evaluation_results.jsonl`.
      Add one for the retry path's summary (≈`workflow.py:1209`) if a retry fixture exists;
      if none exists, cover the retry site through the same helper call and say so in the
      commit body. Implement in `src/evaluation/qa/workflow.py` as two thin call sites to
      `phase_usage_totals` (design D6). One commit (`feat: qa summary records phase usage`).
- [x] 5.2 In `tests/unit/test_compare_runs.py`, add a test (in a new section marked
      `# --- #582: usage keys are additive ---` placed above an existing section marker, not at
      the end of the file) that writes a QA run directory whose rows carry `usage` and one
      identical directory without it, and asserts `cr.load_qa_run` returns equal results for
      both. No change to `compare_runs.py`. One commit
      (`test: compare_runs reads qa rows with usage`).

## 6. Benchmark judge usage

- [x] 6.1 In `tests/unit/test_benchmark_ragas_dialect.py` and
      `tests/unit/test_benchmark_report_records_running_config.py`, add tests, then implement
      design D7 in `src/bin/service_benchmark.py`, one commit
      (`feat: record ragas judge usage per arm`). Assert: a pure `ragas_judge_identity` returns
      the evaluator provider/model and falls back to the benchmark provider/model; with the
      ragas stub's `evaluate` widened to call `on_llm_end` on each `kwargs["callbacks"]`
      entry, `get_ragas_results` over two metrics leaves `self._judge_usage` equal to the sum
      under the judge identity; `handle_results(..., judge_usage=U)` records
      `judge_usage == U` when RAGAS ran and `None` when it did not (key present both times);
      an arm whose metrics all skip ragas records `None`. Wire the run loop (≈`:2255`) to
      reset `self._judge_usage = None` before each arm and pass it to `handle_results`. Keep
      the diff to the helper, the recorder wiring and the one field.

## 7. Docs

- [ ] 7.1 In `docs/docs/evaluation.md`, under `### Cost, concurrency, and data handling`, add a
      `#### Price a run from recorded tokens` subsection: where `usage` appears (the three row
      files, `summary.json` `provenance.usage`, benchmark `judge_usage`), the D3 shape, the
      arithmetic `input_tokens × rate_in + output_tokens × rate_out` summed over `by_model`
      with the reader's own rates, and the limits: `unreported_calls` means the provider sent
      no counts, a call that raised before it finished is not counted, and a retry
      directory's totals include rows copied from its parent. No numeric rates. If `mkdocs`
      is installed, run `mkdocs build --strict -f docs/mkdocs.yml` and read its INFO lines for
      a broken anchor; if it is not installed, say so in the commit body. One commit
      (`docs: price an eval run from recorded tokens`).

## 8. Publish

- [ ] 8.1 Run `bash scripts/gate.sh` on the tip and confirm it exits 0. Run
      `grep -rn usage_metadata src/evaluation/qa/ src/bin/service_benchmark.py src/utils/llm_usage.py`
      and confirm it now prints matches. Confirm `git diff origin/dev --stat -- docs/` lists
      only `docs/docs/evaluation.md`. Push with `git push -u origin fix/issue-582-eval-token-usage`.
      Write the PR body to `/tmp/pr-582.md` with `Closes #582` in the body (never the title),
      the per-task summary, and a "Post-merge operator check" line naming the issue's HUIT
      smoke command. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-582-eval-token-usage --title "eval: record llm token usage in qa and benchmark artifacts" --body-file /tmp/pr-582.md`.
      This task changes no tracked file; it is complete when `gh pr view --repo fasrc/archi
      fix/issue-582-eval-token-usage` prints the PR.
