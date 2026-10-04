# Tasks — report the re-labelling count without a slice row or a baseline label (issue #447)

Every checkbox below is one loop turn and ends **green and committed**. Inside a task: write the
failing tests, watch them fail for the stated reason, write the smallest fix, run
`bash scripts/gate.sh`, commit. Never end a task with the suite red, and never bypass the gate.

Standing notes for every task:

- **Scope.** Edit only `scripts/benchmarking/compare_runs.py`,
  `tests/unit/test_compare_runs.py`, `docs/docs/interpreting_benchmark_results.md`, and this
  change's files. Do not edit `src/`, `bench_out/`, or the control-plane or CI files the
  project rails protect.
- **Coverage will not catch you.** The gate measures `--cov=src`; `scripts/` is outside it, so
  the 80% bar passes whatever you do. The tests are the only protection.
- **Formatting.** Run `black` (24.10.0) and `isort` (6.0.1) on every changed `.py` file
  **before** `git add`. Check `git status` is empty after each commit.
- **Run `python -m pytest`, not bare `pytest`.**
- **Append, do not insert.** New tests go at the END of `tests/unit/test_compare_runs.py`,
  under the banner `# --- issue #447: slice exclusions without a row or a baseline label ---`.
  The file's current last line, `        assert "cannot join" in err and expected in err`, must
  appear unchanged as context in `git diff origin/dev -- tests/unit/test_compare_runs.py`.
- **No duplicate test names.** The file has 128 `def test_` functions today. After each task,
  `grep -c "^def test_" tests/unit/test_compare_runs.py` must equal 128 plus the tests you
  added, and `grep "^def test_" tests/unit/test_compare_runs.py | sort | uniq -d` must print
  nothing.
- Build arms with the `_artifact` fixture and `_row(...)` (`tests/unit/test_compare_runs.py:48`,
  `:114`); `_row` omits any field you do not pass, which is how you get an unlabelled row. Read
  `test_a_failed_baseline_row_does_not_hide_a_relabelling_between_clean_arms` for the shape.

## 1. Count over labelled arms and report at the top level

- [x] 1.1 Add `_slice_membership` and `slice_exclusions`, route `slice_block` through the
      helper, and skip unlabelled rows (design D1, D2). RED first — append these tests:
      (a) `test_slice_exclusions_counts_when_every_question_is_relabelled` — two clean arms,
      questions `q1`, `q2`; baseline `difficulty="hard"`, treatment `difficulty="easy"`, both
      with `faithfulness`. Assert no `difficulty` row in `cr.slice_block(...)` and
      `cr.slice_exclusions(arms[0], arms, ["q1", "q2"])["difficulty"] == 2`.
      (b) `test_slice_exclusions_counts_when_a_group_scores_nothing` — one agreeing question
      whose treatment row has no finite metric (so every pair has `n == 0`) plus one
      re-labelled question; assert the `difficulty` count is 1.
      (c) `test_an_unlabelled_baseline_does_not_hide_a_relabelling` — three clean arms; on
      `q1` the baseline row has no `difficulty`, the other two say `"easy"` and `"hard"`; add a
      `q2` all three label `"easy"`. Assert the `difficulty` count is 1 for **each** of the
      three arms as baseline (reorder `arms` so the chosen arm is passed first and as
      `baseline`).
      (d) `test_an_unlabelled_row_is_not_a_disagreement` — three clean arms on one question:
      `"hard"`, `"hard"`, and a row with no label; assert the count is 0. Repeat with
      `difficulty=None` and `difficulty=""` for the third arm.
      (e) `test_slice_exclusions_lists_every_gated_field_with_zero` — arms that all carry
      `anchor_type` and `difficulty` and agree; assert `slice_exclusions` returns
      `{"anchor_type": 0, "difficulty": 0}` (every `SLICE_FIELDS` entry the arms carry).
      Today (a)-(e) fail with `AttributeError: module ... has no attribute 'slice_exclusions'`,
      and (c) also fails on the count; watch that. Then implement per design D1 and D2. Rewrite
      the `slice_block` docstring paragraph that names #447 (design D4, last paragraph).
      Run `python -m pytest tests/unit/test_compare_runs.py tests/unit/test_benchmark_resilience.py -q`.
      If an existing test fails because it asserted that an unlabelled clean row counts as a
      disagreement, update it per design D2 "Behavior change to watch" and name it in the
      commit message. Any other existing failure is a defect in your change: fix the code, not
      the test. Gate green; commit.

## 2. Carry the count into the report and the renderer

- [ ] 2.1 Add `"slice_exclusions"` to `build_report` and render from it (design D1 last
      paragraph, D3). RED first — append:
      (a) `test_report_json_carries_slice_exclusions_for_every_gated_field` — run the CLI with
      `--json` (or call `cr.build_report` the way the existing `--json` tests do; grep for
      `"--json"` in the test file) over two agreeing arms; assert the JSON has
      `slice_exclusions == {"anchor_type": 0, "difficulty": 0}` (fields the arms carry).
      (b) `test_rendered_report_names_the_relabelling_when_no_slice_survives` — the shape of
      1.1(a); render with `cr.render_markdown(report)`; assert the text contains
      "carry a different `difficulty`" and does not contain "No slice field".
      (c) `test_rendered_report_says_no_slice_field_only_when_the_gate_excludes_all` — arms
      where one arm carries no slice field at all; assert "No slice field" is present.
      Watch (a) and (b) fail. Then implement. Confirm the golden render test
      (`tests/unit/test_compare_runs.py:2305`) still passes unchanged. Gate green; commit.

- [ ] 2.2 Update `docs/docs/interpreting_benchmark_results.md` per design D4. No test. Build
      check: `grep -n "447" docs/docs/interpreting_benchmark_results.md` prints nothing, and
      `grep -n "slice_exclusions" docs/docs/interpreting_benchmark_results.md` prints at least
      one line. Gate green; commit.

## 3. Close out

- [ ] 3.1 Verify, push, and open the PR. Steps, in order:
      1. `bash scripts/gate.sh` exits 0. `git status` is empty.
      2. `git diff origin/dev --stat` lists only the three files in the scope note and this
         change's `openspec/changes/fix-issue-447-slice-exclusions/` files.
      3. `openspec validate fix-issue-447-slice-exclusions --strict` exits 0. If the `openspec`
         CLI is not installed here, skip this step and say so in the PR body — do not install it.
      4. `git push -u origin fix/issue-447-slice-exclusions`. Confirm
         `git ls-remote origin fix/issue-447-slice-exclusions` reports the local `HEAD` SHA.
      5. `gh pr create --repo fasrc/archi --base dev --title "fix(#447): report slice exclusions without a slice row or a baseline label"`.
         The body MUST contain `Closes #447` on its own line, and sections **What**,
         **Why** (gap A and gap B, with the anchors from `proposal.md`), **Behavior change**
         (an unlabelled clean row no longer counts as a disagreement — the operator's
         2026-09-26 decision), and **Coverage caveat** (`scripts/` is outside `--cov=src`; give
         the test count the gate collected). If `gh pr create` against `fasrc/archi` fails with
         a permissions error, leave the branch pushed, do **not** open a PR on any other
         repository, and stop.
      6. Record the PR URL under this task, tick it, and commit that edit through the gate.
         Do not merge.
