# Tasks — strict-JSON migration of the committed benchmark artifacts

Every checkbox below is one loop turn and ends **green and committed**. Write the failing test,
watch it fail, make the smallest change that turns it green, run `bash scripts/gate.sh`, commit.
Never end a task with the suite red, and never use `--no-verify`.

Four standing notes for every task:

- **No `src/` edit.** This is a data PR. The helpers it uses are already merged and correct. If a
  task seems to need a `src/` change, stop and record why under the task instead.
- **Coverage.** The gate measures `--cov=src`, and this diff contains no `src/` line, so
  `diff-cover` reports "No lines with coverage information" and passes. Do not chase a percentage.
  Black and isort **do** enforce `tests/` — run the formatter **before** `git add`, then confirm
  `git status` is empty after the commit.
- **No trailing newline on an artifact.** The harness writes none. Adding one churns all 18 files.
- **Work inside the loop container.** The host has no benchmark toolchain.

## 1. Guard the invariant, then migrate the JSON

- [x] 1.1 Create `tests/unit/test_bench_out_artifacts.py`. It is a NEW file, so there is no
      existing final assertion to displace and no existing `def test_...` name to collide with —
      but check both before writing if the file somehow already exists.

      Resolve the artifact directory from `__file__` (`Path(__file__).resolve().parents[2] /
      "bench_out"`), never from the process working directory, and `pytest.skip` the module if it
      is absent. Parse the 28 MB once in a module-scoped fixture and share it across the tests.

      **RED — two tests, both failing on the current tree:**

      1. Every `bench_out/*.json` parses with `json.loads(text, parse_constant=<raises>)`. Fails on
         10 files today.
      2. For every arm of every artifact, each `total_results` key ending in `_scored` — excluding
         `source_scored_count`, which is the source-accuracy denominator, not a judge-score count —
         equals `f"{finite} of {total}"`, where `total` counts `single_question_results` entries
         whose `status` is `"ok"` or absent, and `finite` counts those same rows whose `<metric>`
         cell is a finite `int` or `float`. Exclude `bool` explicitly: `bool` subclasses `int`, and
         a `matched: true` cell would otherwise count as a score. Fails on 5 strings across 4 files
         today.

      Skip any JSON that is not an artifact (no `dict`, or no list-valued `benchmarking_results`)
      so a foreign file in the directory cannot fail the suite.

      Run the two tests and **watch them fail on 10 and 4 files respectively.** Record the failure
      counts under this task.

      **Observed RED failures:** test 1 failed on 10 files; test 2 failed on 5 strings across 4 files.

      **GREEN — migrate.** Write a throwaway script at `/tmp/migrate_bench_out.py` (outside the
      repository, per the issue). For each `bench_out/*.json`: load with plain `json.loads`,
      recompute every `<metric>_scored` by the rule above, then
      `json.dump(json_safe(obj), f, indent=4, allow_nan=False)` — importing `json_safe` from
      `src.utils.benchmark_schema`, not reimplementing it. Write the file **only if the produced
      text differs from the text on disk**, and write no trailing newline.

      Expected, and each one is a check that fails the task if it does not hold:

      - Exactly **10** files are rewritten, and they are exactly the 10 that `grep -l 'NaN'
        bench_out/*.json` lists.
      - `git status --porcelain bench_out` shows those 10 and nothing else. The other 8 artifacts
        round-trip byte-identical and must not appear.
      - `grep -l 'NaN' bench_out/*.json` is now empty.
      - The 5 corrected strings are exactly these:
        | artifact | key | before | after |
        |---|---|---|---|
        | `benchmarking-ragas-205-20260817_040939.json` | `context_precision_scored` | `109 of 109` | `108 of 109` |
        | `benchmarking-ragas-devbench-20260709_150420.json` | `answer_relevancy_scored` | `26 of 26` | `20 of 26` |
        | `benchmarking-ragas-devbench-20260807_004638.json` | `answer_relevancy_scored` | `106 of 106` | `89 of 106` |
        | `benchmarking-ragas-devbench-20260807_004638.json` | `context_precision_scored` | `106 of 106` | `104 of 106` |
        | `benchmarking-ragas-kbingest-20260709_052330.json` | `answer_relevancy_scored` | `26 of 26` | `17 of 26` |

      Re-run the two tests — both green. Format, `bash scripts/gate.sh`, commit as
      `chore(bench_out): migrate committed artifacts to strict json`. Use a plain single-quoted
      message: a `${...}` in a commit message aborts the commit under `set -u` and leaves the work
      staged while the push reads "up to date".

## 2. Re-render the reports

- [x] 2.1 Re-render the reports for the 10 migrated artifacts only. Pass the 10 paths
      **explicitly** — the script's default glob covers all 18 and would create 8 more markdown
      reports for artifacts whose data did not change:

      ```
      python scripts/benchmarking/backfill_report_provenance.py \
        --regenerate-md --regenerate-html <the 10 migrated json paths>
      ```

      Expected output, each a check: `0 of 10 artifact(s) changed` (every artifact is already
      stamped, so the provenance pass adds no churn), `re-rendered 9 report(s)` (one of the 10,
      `benchmarking-ragas-bench-20260704_183010`, has no `_report.html` and gains none), and
      `rendered 10 markdown report(s)` (no `_report.md` exists in `bench_out/` today, so all 10 are
      new files, about 1.6 MB in total).

      Then extend `tests/unit/test_bench_out_artifacts.py` with a third test: no
      `bench_out/*_report.md` or `bench_out/*_report.html` matches the regular expression `\bnan\b`.
      **Word boundaries are required** — the reports contain the word "maintenance", and a substring
      search reports 21 hits on a file whose real count is 0.

      Add the new test by inserting a complete function; do not append text onto the end of the
      file's last existing test, which silently steals that test's final assertion. Give it a name
      no other test in the file uses.

      Run it: green. It would have failed on 9 files before the re-render.

      Confirm `git status --porcelain bench_out` now lists the 10 JSON, 9 modified `_report.html`,
      and 10 new `_report.md` — 29 paths, nothing else. Format, `bash scripts/gate.sh`, commit as
      `chore(bench_out): re-render reports for the migrated artifacts`.

## 3. Publish

- [x] 3.1 Push and open the PR. Do **not** merge.

      1. `git push -u origin fix/issue-426-bench-out-strict-json`. The `-u` matters: the branch was
         created from `origin/dev` and therefore tracks the trunk until this sets it.
      2. Confirm the push landed on `fasrc/archi`, not on a fork: compare
         `git ls-remote origin fix/issue-426-bench-out-strict-json` against local `HEAD`. If the
         remote SHA is behind or missing, the push did not land — stop and record it.
      3. Open the PR:
         ```
         env -u GH_TOKEN gh pr create --repo fasrc/archi --base dev \
           --title 'chore(#426): migrate committed bench_out artifacts to strict json'
         ```
         The ambient `GH_TOKEN` cannot write; every `gh` write needs the `env -u GH_TOKEN` prefix.

         The body MUST contain `Closes #426` on its own line — a closing keyword in the **title**
         leaves the issue unlinked — and MUST contain these sections:

         **What**: the 10 artifacts rewritten through `json_safe` + `allow_nan=False`, the 5
         recomputed denominators, the reports re-rendered. State that no `src/` file changed.

         **Before / after**: the 5-row table from task 1.1, plus the counts — 10 of 18 artifacts
         carried a bare `NaN` before and 0 do after; the 8 clean artifacts round-trip byte-identical
         and are untouched; `\bnan\b` appeared in 9 committed HTML reports (6 to 36 occurrences
         each) and appears in 0 reports now.

         **Size**: about +1.6 MB, all of it the 10 newly created `_report.md` files. Say plainly
         that no `_report.md` existed in `bench_out/` before, so these are new files rather than
         re-renders, and that a reviewer who would rather not carry them can drop those 10 files
         and the report half of the test without affecting the JSON migration.

         **Gate**: the result, and that `diff-cover` reports no lines with coverage information
         because the diff contains no `src/` line.

      4. Verify the link took: query the PR's `closingIssuesReferences` through the GraphQL API and
         confirm it names #426. Do not infer the link from the body text.
      5. If `gh pr create` against `fasrc/archi` fails with a permissions error, leave the branch
         pushed, do **not** open a PR on any other repository, and stop.
      6. Record the PR URL as a line under this task, tick the task, and commit that edit with the
         gate. Do not merge.

      **PR: https://github.com/fasrc/archi/pull/438** — open against `fasrc/archi:dev`, not merged.
      The GraphQL `closingIssuesReferences` query names #426, so the link is confirmed rather than
      inferred from the body.

      **Outcome — the loop hit the step-5 stop condition; the host completed the publish.**
      Inside the loop container, `git push -u origin fix/issue-426-bench-out-strict-json` returned
      HTTP 403 ("Permission to fasrc/archi.git denied to swinney"). The ambient `GH_TOKEN` is a
      fine-grained PAT scoped to Contents: read, and the git-over-HTTPS push path needs Contents:
      write. The loop obeyed step 5 correctly: it left the branch on the fork (`swinney/archi` at
      `e18d6614`), opened no PR anywhere else, and stopped.

      The wrap-up phase runs on the host, where the `gh` keyring credential carries `repo` scope and
      an SSH key is available. From there `git push -u origin` landed on `fasrc/archi` — the remote
      SHA equals local `HEAD` (`4a9c9b7c`) — and `env -u GH_TOKEN gh pr create` opened PR #438. The
      gate was re-run on the host before the push: black and isort clean, 3898 passed, 2 skipped,
      1 xfailed, and `diff-cover` reports no lines with coverage information because the diff holds
      no `src/` line.

      **Standing note for a future loop turn:** the container credential cannot publish. Publishing
      is a host step until someone grants the PAT Contents: write on `fasrc/archi`.
