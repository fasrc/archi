# Tasks — fix-issue-817-fm-wrapper-signal-during-qa

## 0. Read first (no checkbox)

- **Design of record:** `design.md` (D1-D6) in this folder. The three patches in
  `patches/` are the exact text of the change; Loop 1 built and ran them (design D6).
  Apply them with `git apply -p1 <patch>` from the repository root; do not retype them
  and do not edit them. Each task applies its test half first (the self-test file only),
  runs the self-test and confirms the EXACT red total, then applies the rest and confirms
  the EXACT green total.
- **Scope.** Edit only `scripts/benchmarking/feature_matrix/{lib.sh,qa_arm.sh,test_feature_matrix_wrappers.sh}`,
  `docs/docs/proposals/feature-matrix-campaign-2026.md`, and this `tasks.md`. Do not edit
  `archive_run.sh`, `fm_page`, `fm_die`, `fm_die_paged`, any existing check (1-120), the
  existing `signal-launch.py` launcher, `deploy/`, `config/`, `.github/`, or any
  requirements file. Do not edit `design.md` or `proposal.md` except under a
  "## Measurement" heading at the end of `design.md` (see below).
- **Self-test.** `timeout 600 bash scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`
  from the repository root; about 65-80 s on the host; its last line is `N passed, M failed`.
  Baseline on this branch before task 1.1: `127 passed, 0 failed`. The signal checks give
  the wrapper a process group of its own through their launcher; run the self-test
  directly, not under `setsid`, and do not use `ps` (it is not in the loop image).
- **If a total or a `not ok` line differs** from what a task says, STOP: do not commit,
  put the tree back (`git checkout -- scripts docs`), write what you saw (the command,
  the totals, the `not ok` lines) under a "## Measurement" heading at the end of
  `design.md`, write the question in `docs/questions.md`, and exit.
- **Gate.** Run `bash scripts/gate.sh` in the foreground, with no pipe, no redirect and
  no `&`, and wait for its exit status. Never end a turn while it or `git commit` runs.
  The pre-commit hook runs the same gate; commit only through the hook, never with
  `--no-verify`. The gate runs the self-test too (its `N passed, 0 failed` line is in the
  gate output) and `diff-cover`, which measures only `src/`: a `scripts/`-only diff
  prints "No lines with coverage information in this diff" and passes; that is expected.
- **TDD.** Each checkbox below is ONE commit with the suite green. The failing checks and
  the code that makes them pass go in the SAME commit; put the red run's `not ok` lines
  and both totals in the commit body. Tick the box in the same commit as its work.
- Commit messages: short, lowercase, no `Co-Authored-By` trailer, no AI attribution. Use
  `git commit -F <file>` for a multi-line body, with the file OUTSIDE the repository
  (under `/tmp`).

## 1. The interruptible QA run

- [ ] 1.1 Red then green in one task (do not commit a red state). From the repository
      root, apply the test half of patch A:
      `git apply -p1 --include='scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh' openspec/changes/fix-issue-817-fm-wrapper-signal-during-qa/patches/A-task-1.1.diff`.
      It adds header lines 121-126, the stub's `exec sleep 30` slow mode and its
      `qa-fail` mode, the PID-only launcher `signal-launch-pid.py`, and checks 121-126
      (design D5). Run the self-test and confirm EXACTLY `128 passed, 5 failed`, with
      these `not ok` lines: `qa_arm SIGTERM pid-only (121: launcher=hung leftover: )`,
      the same for `SIGHUP` (122) and `SIGINT` (123), `qa_arm child exit status (rc=1: ...`
      (124), and `qa_arm sweep SIGTERM pid-only (launcher=hung leftover: )` (126). Then
      apply the rest:
      `git apply -p1 --exclude='scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh' openspec/changes/fix-issue-817-fm-wrapper-signal-during-qa/patches/A-task-1.1.diff`.
      It adds `fm_run_interruptible` and `fm__stop_child` (the form without the
      watchdog) to `lib.sh`, the variables `FM_CHILD_PID`, `FM_CHILD_CMD` and
      `FM_CHILD_FAILED`, the `trap ':'` form of `fm__page_on_signal`, the
      `FM_CHILD_FAILED` branch of `fm__page_on_exit`, the new comment above
      `fm_arm_page_on_failure`, and the `fm_run_interruptible` prefix on the three
      `archi qa` calls in `qa_arm.sh` (design D1-D4). Run the self-test and confirm
      EXACTLY `133 passed, 0 failed`. Confirm that `git status --porcelain` lists only
      `lib.sh`, `qa_arm.sh` and the self-test (and this `tasks.md`). Confirm that
      `git diff -- scripts/benchmarking/feature_matrix/qa_arm.sh | grep -c 'fm_run_interruptible "\$FM_ARCHI" qa'`
      prints 3. Tick this box. Run `bash scripts/gate.sh` in the foreground and wait for
      exit 0. Commit the patch and the tick together only when the gate is green
      (message: `fix(feature-matrix): stop the qa run on a signal to the wrapper pid (#817)`).
- [ ] 1.2 Red then green in one task (do not commit a red state). Apply the test half of
      patch B:
      `git apply -p1 --include='scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh' openspec/changes/fix-issue-817-fm-wrapper-signal-during-qa/patches/B-task-1.2.diff`.
      It adds header line 127, the stub's `qa-slow-noterm` mode, and check 127 (a QA
      command that ignores SIGTERM, run with `FM_CHILD_GRACE=1`). Run the self-test and
      confirm EXACTLY `133 passed, 1 failed`, with the one `not ok` line
      `qa_arm grace kill (launcher=hung leftover: )`. Then apply the rest:
      `git apply -p1 --exclude='scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh' openspec/changes/fix-issue-817-fm-wrapper-signal-during-qa/patches/B-task-1.2.diff`.
      It replaces `fm__stop_child` with the watchdog form (design D3), adds
      `FM_CHILD_GRACE="${FM_CHILD_GRACE:-10}"` after the `FM_MAIL_TIMEOUT=` line, and adds
      the `FM_CHILD_GRACE` lines to the knob list in the `lib.sh` header. Run the
      self-test and confirm EXACTLY `134 passed, 0 failed`. Then show that the checks
      guard the code, one TEMPORARY mutation of `lib.sh` at a time, each put back before
      the next (confirm with `git diff -- scripts/benchmarking/feature_matrix/lib.sh`
      that the diff is the patch's again): (a) in `fm__stop_child`, replace the line
      `  kill -s "$1" "$pid" 2>/dev/null || true` with `  :`, run the self-test, and see
      `130 passed, 4 failed` (121, 122, 123, 126, each `launcher=hung leftover`); put it
      back; (b) in the watchdog subshell of `fm__stop_child`, replace the line
      `    kill -KILL "$pid" 2>/dev/null || true; exit 3 ) &` with `    exit 3 ) &`, run
      the self-test, and see `133 passed, 1 failed` (127); put it back. Copy the five
      `not ok` lines into the commit body. (Loop 1 also measured two more mutations,
      design D6 m2 and m3; do not run them.) Confirm that `git status --porcelain` lists
      only `lib.sh` and the self-test (and this `tasks.md`). Tick this box. Run
      `bash scripts/gate.sh` in the foreground and wait for exit 0. Commit the patch and
      the tick together only when the gate is green (message:
      `fix(feature-matrix): kill a qa run that ignores the forwarded signal (#817)`).

## 2. Docs

- [ ] 2.1 Apply patch C:
      `git apply -p1 openspec/changes/fix-issue-817-fm-wrapper-signal-during-qa/patches/C-task-2.1.diff`.
      It replaces, in the "**Paging (#504).**" paragraph of
      `docs/docs/proposals/feature-matrix-campaign-2026.md`, the sentence "A signal sent
      only to the wrapper's own PID (...) takes effect when the current command ends,
      and that can be the end of the QA run." with the new behavior (#817). Confirm that
      `grep -c "own PID" docs/docs/proposals/feature-matrix-campaign-2026.md` prints 0,
      that `grep -c "takes effect when the current command" docs/docs/proposals/feature-matrix-campaign-2026.md`
      prints 0, and that `grep -c "#817" docs/docs/proposals/feature-matrix-campaign-2026.md`
      prints 1 (before the patch: 1, 1, 0). The `summary.json` step of the runbook does
      not repeat the sentence; do not edit it. If `command -v mkdocs` finds it, run
      `mkdocs build --config-file docs/mkdocs.yml --strict --site-dir /tmp/mkdocs-817`
      and confirm it exits 0 with no WARNING line; if it is not installed, say so in the
      commit body. Confirm that `git status --porcelain` lists only the runbook (and this
      `tasks.md`). Tick this box. Run `bash scripts/gate.sh` in the foreground and wait
      for exit 0. Commit only when the gate is green (message:
      `docs(feature-matrix): a signal to the wrapper pid stops the qa run at once (#817)`).

## 3. Verify, push, and open the PR

- [ ] 3.1 Run the self-test and confirm `134 passed, 0 failed`. Run
      `git diff origin/dev...HEAD --stat -- docs/` (three dots) and confirm it lists ONLY
      `docs/docs/proposals/feature-matrix-campaign-2026.md`; remove any other file under
      `docs/` that this branch added. If `command -v openspec` finds the CLI, run
      `openspec validate fix-issue-817-fm-wrapper-signal-during-qa --strict` and confirm
      it passes; if not, skip it (Loop 1 validated the change on the host). Tick this
      box, commit the tick through the hook (message: `mark #817 tasks complete`), then
      push and open the PR, so the push carries every commit. Push with
      `git push -u origin fix/issue-817-fm-wrapper-signal-during-qa`. Open the PR with
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-817-fm-wrapper-signal-during-qa`,
      the title `fix(feature-matrix): stop the qa run on a signal to the wrapper pid (#817)`,
      and `Closes #817` in the BODY (not the title). Pass the body with a file under
      `/tmp`; do NOT commit a PR-body file to the repository. In the body: list each
      acceptance criterion of #817 with the check that proves it (121-123 for the three
      signals to the wrapper's PID; 124-125 for the exit status and paging off; 126 for
      sweep mode; 127 for the grace-period KILL; 98-112 unchanged for the #624
      behavior); the self-test counts (127 on `origin/dev`, 134 at the tip); the four
      mutations of design D6 with their failing checks; the deviations from the issue
      text (proposal: no `archive_run.sh` step, the vacuous grep criterion and the real
      greps, the branch name, the `FM_CHILD_FAILED` record instead of the ERR trap); the
      new knob `FM_CHILD_GRACE`; the behavior changes (the page body names the command
      and its fate; the QA run starts through `FM_PYTHON` with SIGINT at its default);
      the design "Risks / Trade-offs"; and the note that `scripts/` has no coverage gate,
      so the named checks are the only proof. Then link the PR from the #782 thread the
      issue names: post one comment on PR #782 (`gh pr comment 782 --repo fasrc/archi --body "<text>"`)
      that names the new PR and says it is the fix the thread deferred to #817. No
      `Co-Authored-By` trailer. Do not merge.
