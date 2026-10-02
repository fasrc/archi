## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-434-qa-arm-fail-closed` exists, cut from `origin/dev` at
      `570814d0`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-434-qa-arm-fail-closed --strict` passed on the host.
      The `openspec` CLI is **not usable in this container** — do not run it, and do not add
      a task that does.
- [x] 0.3 The gap is measured at `570814d0`: `grep -c fm_require_scored
      scripts/benchmarking/feature_matrix/qa_arm.sh` prints `0`, and both ledger paths
      (`qa_arm.sh:93` sweep, `:172` single-arm) append without reading `summary.json`.
- [x] 0.4 Every design decision is made in `design.md` (D1–D5). Follow it; where the issue
      body and the design differ, the design wins.

## Rules that apply to EVERY task below

- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed. Write the failing checks,
  run the self-test and watch them fail, make them pass, then commit — all in one task.
- Every task except 2.1 ends with exactly one commit (tick its checkbox in this file in the
  same commit). Commit messages: short, lowercase. Never `git commit --no-verify`. Never add
  a `Co-Authored-By` or session trailer.
- Run the self-test with `bash scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`.
  It must end `N passed, 0 failed`. Never delete or weaken an existing check.
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
- [ ] 1.2 Add checks 60–64 (design D4) to `test_feature_matrix_wrappers.sh` after check 59,
      and fix the header block (design D5). Run the self-test and confirm 60, 61, 62 and the
      refusal half of 64 fail (63 can already pass). Then add `fm_require_scored` to `lib.sh`
      (design D1) and call it in both `qa_arm.sh` paths (design D2). Run the self-test: it
      must end `0 failed`. Confirm `grep -c fm_require_scored
      scripts/benchmarking/feature_matrix/qa_arm.sh` prints `2`. Run `bash scripts/gate.sh`
      and confirm it exits 0. Commit (`fix(#434): qa_arm refuses a run that scored nothing`).

## 2. Publish

- [ ] 2.1 Confirm `git diff origin/dev --stat` lists only the three feature-matrix scripts
      and files under `openspec/changes/fix-issue-434-qa-arm-fail-closed/`. Push with
      `git push -u origin fix/issue-434-qa-arm-fail-closed`. Write the PR body to
      `/tmp/pr-434.md` with `Closes #434` in the body (never the title), a per-task summary,
      and this operator note: "`scripts/` is in the campaign's locked code tree; a running
      feature-matrix campaign needs `--relock` after this merges and the host redeploys.
      `bench_out/feature_matrix/check_qa_scored.sh` can be retired separately." Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-434-qa-arm-fail-closed --title "fix(#434): qa_arm fails closed on a qa run that scored nothing" --body-file /tmp/pr-434.md`.
      This task changes no tracked file; it is complete when `gh pr view --repo fasrc/archi
      fix/issue-434-qa-arm-fail-closed` prints the PR.
