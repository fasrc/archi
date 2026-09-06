# Tasks — bank difficulty in the per-question result

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail, write the smallest fix, run `bash scripts/gate.sh`, commit. Never end
a task with the suite red, and never use `--no-verify`. No `Co-Authored-By` trailer.

Four standing notes for every task:

- **Append tests at the true end of the file.** `tests/unit/test_benchmark_resilience.py`
  ends at line 651 with an `assert paired[0].winner_by_metric == {"faithfulness": "tie"}`.
  A new test inserted *above* that final line silently steals the last assertion from the
  test that owns it. After writing, read the diff's trailing context and confirm that
  assertion is still the last line of its own test.
- **Give every new test a name no other test in the repository uses.** The gate runs no
  linter for redefinition, so a duplicate `def test_...` deletes the earlier one and the
  suite still passes.
- **Both files this change edits are black-clean at `3170498c`.** Keep them that way:
  format before `git add`, and confirm `git status` is empty after the commit. The
  pre-commit black is a writer, CI's is an assert.
- **Scope.** Do not touch `src/utils/benchmark_argilla.py`, any question bank, anything
  under `scripts/`, or the control plane (`deploy/**`, `.github/workflows/**`,
  `ralph.conf`, `PROMPT.md`, `Makefile`, `Containerfile`, `hooks/**`, `config/**`).

## 1. Copy the field

- [ ] 1.1 `model: opus` — RED then GREEN, one commit. In
      `tests/unit/test_benchmark_resilience.py`, beside the existing
      `_answer_and_score_question` tests (the `_StubBenchmarker` seam at :91, `_QITEM` at
      :109), add two tests: one runs a question item carrying `difficulty: "hard"` through
      `agent._answer_and_score_question(item, 1, _MODES)` and asserts
      `bundle["q_results"]["difficulty"] == "hard"`; the sibling runs `_QITEM` (which
      carries no `difficulty`) and asserts `"difficulty" not in bundle["q_results"]`.
      Build the first item as a fresh dict — do **not** mutate `_QITEM`, which other tests
      in the file share. Run
      `python -m pytest tests/unit/test_benchmark_resilience.py -q -k difficulty` and watch
      the first test fail. Then in `src/bin/service_benchmark.py`, directly below the
      `q_results["anchor_type"] = (...)` block at :1911-1915, add the conditional copy:
      when `question_item` is a dict **and** `"difficulty"` is in it, set
      `q_results["difficulty"] = question_item["difficulty"]`; otherwise write nothing.
      Do not give it the `""` fallback `anchor_type` has — `design.md` explains why.
      `bash scripts/gate.sh` green; commit.

## 2. Guard the producer/consumer key

- [ ] 2.1 `model: sonnet` — Seam guard, one commit. Add a test asserting the key the
      harness writes is one of the paired-comparison tool's slice fields: run a
      `difficulty`-bearing item through `_answer_and_score_question`, then reach
      `SLICE_FIELDS` through the plain package import `tests/unit/test_compare_runs.py:35`
      already uses — `from scripts.benchmarking import compare_runs as cr`, read as
      `cr.SLICE_FIELDS`. No `importlib` machinery: the package import works. Assert
      `"difficulty" in cr.SLICE_FIELDS` **and** that the same string is a key of
      `bundle["q_results"]`. This passes once 1.1 has landed — that is the point of it, so
      do not contrive a failure first. Assert only the key agreement; do **not** assert
      which values a bank emits or what a slice prints. `bash scripts/gate.sh` green;
      commit.

## 3. Correct the documentation that says the slice is unavailable

- [ ] 3.1 `model: sonnet` — Docs only, one commit. Three edits, no code:
      (a) `docs/docs/interpreting_benchmark_results.md:578-586` — the admonition titled
      "The `difficulty` slice needs a bank that reaches the artifact" currently states the
      harness copies only `anchor_type` and that artifacts carry no `difficulty`. That is
      false after task 1.1. Rewrite it to say the harness propagates `difficulty` when the
      bank row carries it, that a bank without the field still produces no key and so
      still skips the slice, and that the FASRC bank
      (`fasrc_ragas_queries.json`) has no `difficulty` today while
      `ragas-jeopardy-master.json` does.
      (b) `docs/docs/interpreting_benchmark_results.md:685` — Procedure D's field tree
      already lists `difficulty # bank rows only: easy|medium|hard`; extend that comment to
      record that the harness started writing it in the change closing #431.
      (c) `docs/docs/benchmarking.md` — the field table under "Preparing the Queries File"
      (header at :61) gains a `difficulty` row: not required, "Optional bank label; copied
      verbatim into `single_question_results` when present, so `compare_runs.py` can slice
      by it."
      A docs-only diff touches no `src/` line, so `diff-cover` reports no lines with
      coverage information and the gate passes on the test suite alone. `bash
      scripts/gate.sh` green; commit.

## 4. Close out

- [ ] 4.1 `model: haiku` — Run `bash scripts/gate.sh` once more on the finished change and
      confirm it exits 0. Confirm `git status` is empty. Push with
      `git push -u origin fix/issue-431-propagate-bank-difficulty` — the branch tracks
      `origin/dev`, so `-u` is required or the push retargets the trunk. Open the PR with
      `gh pr create --repo fasrc/archi --base dev`, and put `closes #431` in the **body**
      (a closing keyword in the title does not link the issue; verify the link afterwards).
      Then stop. Do not merge.
