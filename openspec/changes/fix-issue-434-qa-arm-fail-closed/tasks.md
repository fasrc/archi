## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-434-qa-arm-fail-closed` exists, cut from `origin/dev` at
      `570814d0`. On 2026-10-23 Loop 1 merged `origin/dev` `e2978a1d` into it (merge commit
      `7690ad41`, no conflicts). You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-434-qa-arm-fail-closed --strict` passed on the host.
      The `openspec` CLI is **not usable in this container** — do not run it, and do not add
      a task that does.
- [x] 0.3 The gap is measured after the merge: `grep -c fm_require_scored
      scripts/benchmarking/feature_matrix/qa_arm.sh` prints `0`, and both ledger paths
      (`qa_arm.sh:93` sweep, `:172` single-arm) append without reading `summary.json`. The
      drift check just above each append (`:92`, `:171`) now calls `fm_die_paged` (#621).
- [x] 0.4 Every design decision is made in `design.md` (D1–D6). Follow it; where the issue
      body and the design differ, the design wins.
- [x] 0.5 Baseline after the merge (2026-10-23, host): the self-test ends `96 passed, 0 failed`.
      The highest check number in the file is **89** (the header block lists up to 86). The
      last check block in the file is check 82, just before the final `printf ... passed`.
      Loop 1 appended two probe runs at that position in a scratch copy: the check 58
      single-arm shape and `qa_arm.sh --sweep "$SW/configs" --stack r0 --arm
      fasrc-docs-r0b-icl --run 7` each exited 0 and added one ledger row.

## Rules that apply to EVERY task below

- **Run the gate in the foreground.** Run `bash scripts/gate.sh` as a normal command and
  wait for it to exit. Do not start it in the background. Do not end a turn while it runs.
  (On 2026-10-02 a run started the gate in the background, ended the turn to wait for it,
  and the loop stalled with no commit.)
- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed. Write the failing checks,
  run the self-test and watch them fail, make them pass, then commit — all in one task.
- Every task ends with at least one commit (tick its checkbox in this file in the same
  commit; 2.1 says when). Commit messages: short, lowercase. Never `git commit --no-verify`. Never add
  a `Co-Authored-By` or session trailer.
- Run the self-test with `timeout 600 bash scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`,
  in the foreground. It must end `N passed, 0 failed`. Never delete or weaken an existing check.
- Edit only `scripts/benchmarking/feature_matrix/lib.sh`, `qa_arm.sh`,
  `test_feature_matrix_wrappers.sh`, and this `tasks.md`. Do not edit `scripts/gate.sh`,
  `archive_run.sh`, anything under `src/`, `docs/`, or `bench_out/`, or any control-plane
  file. The PR body goes in `/tmp`, never in the repo. Do not add a `docs/questions.md` entry.
- After each commit, `git status --porcelain` must be empty.

## 1. Fail closed on a QA run that scored nothing

- [x] 1.1 Extend the stub `archi` in `test_feature_matrix_wrappers.sh` to write
      `summary.json` exactly as design D3 says (default `scored: 3`; `$T/qa-no-summary` and
      `$T/qa-summary` control files; written on the single-arm `--output-dir` call and on
      `eval qa score <dir>`, never on `eval qa run` or `eval qa prepare`). Run the
      self-test: it must still end `0 failed` with the same passed count as before. Commit
      (`test: stub archi writes a qa summary.json`).
- [ ] 1.2 Add checks 90–94 (design D4) to `test_feature_matrix_wrappers.sh` after the check 82
      block (the last block in the file), before the final `printf ... passed`, and fix the
      header block (design D5). Each new check prints exactly one `ok` or `not ok` line. Run
      the self-test and confirm 90, 91, 92 and 94 fail (93 can already pass). Then add
      `fm_require_scored` to `lib.sh` (design D1, D6) and call it in both `qa_arm.sh` paths
      (design D2). Run the self-test: it must end `101 passed, 0 failed`. Confirm `grep -c
      fm_require_scored scripts/benchmarking/feature_matrix/qa_arm.sh` prints `2`. Run
      `bash scripts/gate.sh` in the foreground and confirm it exits 0. Commit
      (`fix(#434): qa_arm refuses a run that scored nothing`).

## 2. Publish

- [ ] 2.1 Run `git fetch origin dev`. If `git merge-base --is-ancestor origin/dev HEAD`
      fails, run `git merge --no-edit origin/dev`. If that merge conflicts, run `git merge
      --abort`, do not merge, and say so in the PR body. If the merge brings new checks into
      `test_feature_matrix_wrappers.sh` with numbers 90 or higher, renumber checks 90–94
      after the new highest number (in the file, the header, this `tasks.md`, and
      `design.md`), run the self-test and `bash scripts/gate.sh`, and commit
      (`test(#434): renumber the scored checks after the merge`). Then confirm `git diff
      origin/dev --stat` lists only the three feature-matrix scripts and files under
      `openspec/changes/fix-issue-434-qa-arm-fail-closed/`. Tick this 2.1 box in this file
      and commit it (`chore(#434): tick the publish task`) BEFORE the push, so the PR gets
      no second push. Push with
      `git push -u origin fix/issue-434-qa-arm-fail-closed`. Write the PR body to
      `/tmp/pr-434.md` with `Closes #434` in the body (never the title), a per-task summary,
      and these operator notes: "`scripts/` is in the campaign's locked code tree; a running
      feature-matrix campaign needs `--relock` after this merges and the host redeploys.
      `bench_out/feature_matrix/check_qa_scored.sh` can be retired separately." and "The
      new refusal does not page (design D6): the #504 paging spec names only the two drift
      exits for `qa_arm.sh` and says `fm_require_*` checks never page; #624 owns paging on
      other failures after long work." Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-434-qa-arm-fail-closed --title "fix(#434): qa_arm fails closed on a qa run that scored nothing" --body-file /tmp/pr-434.md`.
      The task is complete when `gh pr view --repo fasrc/archi
      fix/issue-434-qa-arm-fail-closed` prints the PR.
