# Tasks — a failed row is not evidence of a bank edit (issue #441)

Every checkbox below is one loop turn and ends **green and committed**. Write the failing test,
watch it fail for the stated reason, write the smallest fix, run `bash scripts/gate.sh`,
commit. Never end a task with the suite red, and never bypass the gate.

Standing notes for every task:

- **Scope.** The only production file to edit is `scripts/benchmarking/compare_runs.py`. The
  only test file to edit is `tests/unit/test_compare_runs.py`. Do not edit
  `src/utils/benchmark_resilience.py`, `src/bin/service_benchmark.py`,
  `tests/unit/test_benchmark_resilience.py`, `bench_out/**`, or any page under `docs/` — the
  first three belong to open PR #440 and the fourth to open PR #438, and no docs page mentions
  this counter (design D6). Do not edit the control-plane or CI files the project rails
  protect.
- **Coverage will not catch you.** The gate measures `--cov=src`, and neither changed path is
  under `src/`, so `diff-cover` scores an empty measurement and the 80% bar passes whatever
  you do. The tests are the only protection. Write them first, and read the collected count.
- **Formatting.** `scripts/*.py` and `tests/*.py` are both inside the gate's enforced format
  scope. Both files are black 24.10.0 and isort 6.0.1 clean today. Run `black` and `isort` on
  both files **before** `git add`, so the pre-commit writer cannot leave content out of the
  commit, and check `git status` is empty after each commit.
- **Run `python -m pytest`, not bare `pytest`**, so an editable install cannot resolve `src`
  to a different checkout.
- **Append, do not insert.** New tests go at the END of `tests/unit/test_compare_runs.py`,
  under a comment banner naming issue #441. The file's current last line,
  `        assert len(re.findall(r"(?<!\\)\|", line)) == 7`, must appear unchanged as trailing
  context in `git diff origin/dev -- tests/unit/test_compare_runs.py`. Nightly runs have
  inserted tests above a file's final line and swallowed the previous test's last assertion.
- **No duplicate test names.** The gate runs no linter, so a reused `def test_...` silently
  deletes the earlier test while staying green. The file has 83 tests today; after task 1.2 it
  must collect 83 + the number you added, and you must check that number.
- **Do not weaken the existing guard.**
  `test_a_slice_drops_questions_whose_field_value_disagrees_between_arms`
  (`tests/unit/test_compare_runs.py:1572`) is the genuine-relabelling test. It must pass
  unchanged at every commit.

## 1. The consumer fix

- [x] 1.1 Add `Arm.has_clean_row` and route `Arm.is_scorable` through it, in
      `scripts/benchmarking/compare_runs.py`. RED first: append to
      `tests/unit/test_compare_runs.py`, under a
      `# --- issue #441: a failed row is not a bank relabelling ---` banner, a test named
      `test_has_clean_row_is_the_status_half_of_is_scorable` that builds one `cr.Arm` directly
      (follow `_slice_arm` in `tests/unit/test_benchmark_resilience.py` for the constructor
      call, but keep the new test in `test_compare_runs.py`) with four rows:
      `{"status": "ok", "faithfulness": 0.5}`, `{"faithfulness": 0.5}` (no `status` key),
      `{"status": "failed"}`, and `{"status": "degraded", "faithfulness": 0.9}`. Assert
      `has_clean_row` is `True` for the first two, `False` for the last two, `False` for a
      question absent from `rows`, and that for each of those five questions
      `arm.is_scorable(question, "faithfulness")` still returns what it returns today
      (`True`, `True`, `False`, `False`, `False`). The test fails today with
      `AttributeError: 'Arm' object has no attribute 'has_clean_row'`; watch that. Then
      implement per design D2: add

          def has_clean_row(self, question: str) -> bool:
              """Whether this arm ran the question to completion."""
              row = self.rows.get(question)
              return row is not None and row.get("status", "ok") == "ok"

      directly above `is_scorable`, and rewrite `is_scorable`'s body to
      `if not self.has_clean_row(question): return False` then
      `return is_finite(self.rows[question].get(metric))`. Keep the `"ok"` default exactly —
      an unmarked legacy row is clean. Do not change `has_metric` (design D4). Run
      `python -m pytest tests/unit/test_compare_runs.py tests/unit/test_benchmark_resilience.py -q`
      and confirm 118 + your new tests pass. Gate green; commit.

- [x] 1.2 Skip an unclean question in `slice_block`'s membership loop, and document the rule.
      RED first: append three tests after the 1.1 tests, each building arms with the
      `_artifact` fixture and calling `cr.slice_block(arms[0], arms, questions, {})` — read
      `test_a_slice_drops_questions_whose_field_value_disagrees_between_arms`
      (`tests/unit/test_compare_runs.py:1572`) for the shape, and note that `_row(...)` omits
      any field you do not pass, which is how you get the pre-#440 failure row:
      (a) `test_a_question_that_failed_in_one_arm_is_not_a_bank_relabelling` — baseline rows
      `_row("ok1", anchor_type="reasoning", difficulty="easy", faithfulness=0.5)`,
      `_row("ok2", ...same..., faithfulness=0.5)`,
      `_row("bad", anchor_type="reasoning", difficulty="hard", faithfulness=0.5)`; treatment
      rows the same two clean questions with `faithfulness=0.6` and, for `"bad"`,
      `_row("bad", status="failed")` with **no** `anchor_type` and **no** `difficulty`. Assert
      `excluded_mismatched` is 0 for **every** row of the block, and assert it separately for
      `anchor_type` and for `difficulty` so a missing field cannot make the assertion vacuous.
      Assert `"bad"` is in no slice's membership by checking the `easy` slice's `n` is 2. Today
      both fields report `excluded_mismatched == 1`; watch that.
      (b) `test_a_degraded_row_is_not_a_bank_relabelling` — the same shape with
      `status="degraded"` on the treatment row for `"bad"`, asserting 0 for both fields. Fails
      today.
      (c) `test_a_relabelled_question_is_still_counted_when_both_arms_are_clean` — the
      genuine case: `"agreed"` labelled `easy` in both arms, `"relabelled"` labelled `easy` in
      the baseline and `hard` in the treatment arm, both clean successes. Assert
      `excluded_mismatched == 1` for `difficulty` and that the only reported value is `easy`
      with `n == 1`. This one **passes today** — it is the over-reach guard, so do not contrive
      a failure for it; state in its docstring that it must pass before and after.
      Then implement per design D3: in `slice_block`, replace the
      `if any(arm.rows.get(question, {}).get(field) != value for arm in arms):` line with

          if not baseline.has_clean_row(question):
              continue
          ran_it = [arm for arm in arms if arm.has_clean_row(question)]
          if any(arm.rows[question].get(field) != value for arm in ran_it):

      Order matters — below the mismatch test the guard is dead code.

      **Amended 2026-09-07 (`d8155543`), after `329b893a` shipped the wrong form.** This step
      first said to insert `if not all(arm.has_clean_row(question) for arm in arms): continue`
      above the mismatch test. That gates the whole question on every arm, which fixes the
      two-arm case and breaks the sweep case: one unrelated arm failing then hides a
      re-labelling another arm genuinely carries and shrinks that arm's slice `n`. Filter the
      arms instead, and drop the question only when the **baseline** row is unclean, because
      the baseline's value is the group key. Add the two three-arm tests named in design D3
      (`test_a_relabelling_survives_a_third_arm_failing_the_same_question`,
      `test_a_third_arms_failure_does_not_shrink_another_arms_slice`) — each one fails under
      the `all(...)` form and passes under this one. Do not restore the `all(...)` form.

      **Amended 2026-09-09, after `1e06a958` shipped the guard in the wrong order.** Put the
      mismatch test **first** and let it read only `ran_it`, comparing those arms against each
      other; put the baseline guard **below** it:

          ran_it = [arm for arm in arms if arm.has_clean_row(question)]
          if len({arm.rows[question].get(field) for arm in ran_it}) > 1:
              mismatched += 1
              continue
          if not baseline.has_clean_row(question):
              continue

      The earlier order was dead code only for the pre-#440 shape. Once #431 lands a failed
      row keeps its bank fields, so an unclean *baseline* row carries a label, clears the
      `isinstance(value, str)` guard, and is dropped before any comparison — hiding a
      relabelling two clean arms carry. Measured on `1e06a958`: `excluded_mismatched` was 0
      with the failed arm as baseline and 1 with either clean arm as baseline, over identical
      artifacts, so the count moved with `--baseline`. Add
      `test_a_failed_baseline_row_does_not_hide_a_relabelling_between_clean_arms` (fails under
      the earlier order) and
      `test_an_unclean_baseline_row_whose_arms_agree_is_dropped_without_a_count` (the
      over-reach guard — it must pass before and after; do not contrive a failure for it).
      Do not restore the baseline guard above the mismatch test.

      Add a paragraph to the `slice_block` docstring, after the paragraph that begins
      "Membership needs **every** arm", recording that an arm which did not run the question to
      completion is skipped in the comparison rather than counted as a re-labelling, that its
      own `status` is the evidence, that the skip is per arm and not per question because a
      sweep expands into three or more arms, and that a question whose baseline row is unclean
      is dropped outright. Run
      `python -m pytest tests/unit/test_compare_runs.py tests/unit/test_benchmark_resilience.py -q`,
      confirm every test passes including
      `test_a_slice_drops_questions_whose_field_value_disagrees_between_arms` and
      `test_slices_appear_only_for_fields_present_in_both_arms`, and read the collected count
      to confirm it equals 118 plus the tests you added. Gate green; commit.

## 2. Close out

- [x] 2.1 Verify, push, and open the PR. Steps, in order:
      1. `bash scripts/gate.sh` on the finished branch exits 0. `git status` is empty.
      2. `git diff origin/dev --stat` lists only `scripts/benchmarking/compare_runs.py`,
         `tests/unit/test_compare_runs.py`, `docs/docs/interpreting_benchmark_results.md`, and
         this change's `openspec/changes/fix-issue-441-failed-arm-not-relabelling/` files.
         Confirm `git diff origin/dev -- src/ bench_out/` prints nothing.
         (Amended 2026-09-08: the docs page was added to the scope in review round 2, per
         `AGENTS.md:54`. The step first required `docs/` to be empty.)
      3. `openspec validate fix-issue-441-failed-arm-not-relabelling --strict` exits 0. If the
         `openspec` CLI is not installed in this environment, skip this step and say so in the
         PR body — do not install anything.
      4. Push: `git push -u origin fix/issue-441-failed-arm-not-relabelling`. Confirm
         `git ls-remote origin fix/issue-441-failed-arm-not-relabelling` reports the same SHA
         as local `HEAD`; a push that reports "up-to-date" without matching SHAs did not land.
      5. Open the PR with
         `gh pr create --repo fasrc/archi --base dev --title "fix(#441): stop counting a failed question as a bank relabelling"`.
         The body MUST contain `Closes #441` on its own line — a closing keyword in the title
         does not link the issue — and MUST contain these sections:
         **What** (the `has_clean_row` predicate, the one guard above the mismatch test, the
         docstring rule);
         **Why the number was dishonest** (a failure row carries no bank field, so
         `None != "hard"` counted it; the renderer then names a bank edit as the cause);
         **Measured** (the five-shape table from `design.md`: `excluded_mismatched` 1 → 0 on
         the pre-#440 shape and on a degraded row, 1 → 1 on genuine relabelling, and every
         other slice key identical in all five shapes);
         **Scope against the open PRs**: #440 (#431) is the producer side and shares no file
         with this PR; #438 (#426) touches `bench_out/**` only; checked by file list;
         **Coverage caveat**: the gate measures `--cov=src` and neither changed path is under
         `src/`, so patch coverage is an empty measurement here and the named tests are the
         protection — state the test count the gate collected.
         If `gh pr create` against `fasrc/archi` fails with a permissions error, leave the
         branch pushed, do **not** open a PR on any other repository, and stop.
      6. Record the PR URL as a line under this task, tick the task, and commit that edit with
         the gate. Do not merge.

      **PR:** https://github.com/fasrc/archi/pull/443 — base `dev`, closes #441.
      Steps 1-4 ran in the loop; `gh pr create` (step 5) failed there with
      `Resource not accessible by personal access token`, so the loop stopped as step 5
      instructs. The nightly wrap-up re-verified steps 1-3 on the host
      (gate green: 3899 passed, 2 skipped, 1 xfailed; diff scope as specified;
      `openspec validate --strict` exits 0), pushed the branch to `origin` with a matching
      remote SHA, and opened the PR from the host.
