## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-503-gold-extraction-retry` exists, cut from `origin/dev` at
      `db701852`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-503-gold-extraction-retry --strict` passed on the host.
      The `openspec` CLI is **not usable in this container** — do not run it, and do not add
      a task that does. It is already green.
- [x] 0.3 Every design decision is made in `design.md` (D1–D6). Follow the design; where the
      issue body and the design differ, the design wins.

## Rules that apply to EVERY task below

- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed. Write the failing test,
  watch it fail, make it pass, then commit — all in one task.
- **Every task except 4.1 ends with exactly one commit** (tick its checkbox in this file in
  the same commit). Do not add a task that changes no file.
- Do not implement a later task's behaviour early. Each task's red must still fail when that
  task starts.
- Additive only: never rename or remove an existing key, status, or exit code. A record with
  no retry must serialise exactly as it does on `origin/dev`.
- Before you add a test, `grep -n 'def test_' <file>` and pick a name that is not already
  used. No linter runs, so a duplicate `def test_...` silently replaces the older test.
- Never append a test at the very end of an existing file without checking the diff: after
  each insert, `git diff` the file and confirm the test above yours still ends with its own
  `assert`.
- Never delete a test. After each commit,
  `git diff origin/dev -- tests/ | grep -c '^-.*def test_'` must print `0`.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. After each
  commit, `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer. Commit
  messages: short, lowercase.
- Do not edit `src/cli/qa_eval.py`, the `retry` phase logic in `workflow.py` (only its
  `build_summary` call gets the new argument), or any control-plane file. Do not write
  under `docs/`. The PR body goes in `/tmp`, never in the repo. Do not add an entry to
  `docs/questions.md`.

## 1. The record field and the row reader

- [ ] 1.1 Add `gold_extraction_attempts` to `PreparationRecord` and to `_record_from_row`
      (design D2, D3). Tests first in `tests/unit/evaluation/qa/test_preparation.py`, then
      code, one commit (`feat: record gold extraction attempts on preparation rows`). Cover:
      (a) a prepared record and a `preparation_failed` record built with
      `gold_extraction_attempts=2` emit the key in `to_dict()`, and round-trip through
      `preparation_record_from_dict` to an equal record;
      (b) a record with the default `None` has no `gold_extraction_attempts` key in
      `to_dict()`;
      (c) `True`, `0`, and `"2"` raise `ValueError` in `__post_init__`;
      (d) a row without the key still loads (old runs).

## 2. The one-time retry

- [ ] 2.1 Retry `extract_gold` once on a `validate_gold_output` `ValueError` in
      `prepare_dataset_item` (design D1, D4). Tests first in `test_preparation.py`, then code,
      one commit (`fix: retry a gold extraction once after a shape violation`). Use a fake
      extractor class that returns a queue of results and counts calls (and, for (e), sets
      `last_usage` per call). Cover:
      (a) first result `{"atoms": "not a list"}`, second valid → `prepared`,
      `atom_source == "inferred"`, `gold_extraction_attempts == 2`, exactly 2 calls;
      (b) two shape violations → `preparation_failed`, `gold_extraction_attempts == 2`,
      exactly 2 calls;
      (c) `extract_gold` raises `RuntimeError` on the first call → `preparation_failed`,
      exactly 1 call, no `gold_extraction_attempts` key in `to_dict()`;
      (d) `extract_gold` raises `ValueError` itself on the first call → exactly 1 call
      (only the validator's `ValueError` retries);
      (e) usage: first call `last_usage` with 100 input tokens and 1 call, second with 50 and
      1 → the record's `usage["input_tokens"] == 150` and `usage["calls"] == 2`; build each
      snapshot in the `src/utils/llm_usage.py` shape and merge with `sum_usage`;
      (f) a live item whose resolver raises `OracleResolutionError` → `preparation_failed`
      with 0 extractor calls and no `gold_extraction_attempts` key (find the existing live
      fakes with `grep -rn OracleResolutionError tests/unit/evaluation/qa/`);
      (g) a valid first result → exactly 1 call and no `gold_extraction_attempts` key.
      Log one `WARNING` before the retry with the item id. Do not put answer text in the log.
      All existing `test_preparation.py` tests must pass unchanged.

## 3. Coverage line in the summary and the report

- [ ] 3.1 Add `items_total` and `scored_items` to `build_summary`, the report header line,
      and pass `items_total` from both `build_summary` callers in `workflow.py` (`:858` score
      phase, `:1213` retry phase) (design D5, D6). Tests first, then code, one commit
      (`feat: report scored items against total items`). Cover:
      (a) in `test_scoring.py`: `build_summary(..., items_total=3)` over 3 preparation
      records with 1 `preparation_failed` and scored results for the other 2 → `items_total`
      3 and `scored_items` 2; with no `items_total` argument → `items_total` is `None`;
      an item whose only attempt is `execution_failed` is not counted in `scored_items`;
      (b) `_report_header` with a summary that has both keys prints
      ``- Scored items: `2` / items: `3` ``; with a summary that has neither key it prints
      `unavailable` for each and does not raise;
      (c) in `test_workflow.py`: an end-to-end `QAWorkflow().composite(...)` run (copy the
      fixture pattern near `test_workflow.py:600-644`) whose dataset has one item that fails
      preparation → `summary.json` has `items_total` equal to the dataset size and
      `scored_items` one less, and `report.md` contains the `Scored items:` line.
      Every existing summary key keeps its value; existing scoring and workflow tests pass
      unchanged.

## 4. Publish

- [ ] 4.1 Run `bash scripts/gate.sh` on the tip and confirm it exits 0 with patch coverage
      ≥ 80 %. Confirm `git diff origin/dev --stat -- docs/` prints nothing. Push with
      `git push -u origin fix/issue-503-gold-extraction-retry`. Write the PR body to
      `/tmp/pr-503.md` with `Closes #503` in the body (never the title) and a per-task
      summary. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-503-gold-extraction-retry --title "eval qa: retry a malformed gold extraction once and report scored / items" --body-file /tmp/pr-503.md`.
      This task changes no tracked file; it is complete when `gh pr view --repo fasrc/archi
      fix/issue-503-gold-extraction-retry` prints the PR.
