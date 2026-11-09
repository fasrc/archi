# Tasks — fix-issue-824-fm-launch-window

## 0. Read first (no checkbox)

- **Design of record:** `design.md` (D1-D5) in this folder. The two patches in `patches/`
  are the exact text of the change; Loop 1 built and ran them (design D5). Apply them with
  `git apply -p1 <patch>` from the root of the tree you are in; do not retype them and do
  not edit them.
- **Scope.** Edit only `scripts/benchmarking/feature_matrix/{lib.sh,test_feature_matrix_wrappers.sh}`,
  `openspec/changes/fix-issue-817-fm-wrapper-signal-during-qa/design.md`, and this
  `tasks.md`. Do not edit `qa_arm.sh`, `archive_run.sh`, any existing check (1-129), any
  existing launcher, `src/`, `docs/`, `deploy/`, `config/`, `.github/`, or any requirements
  file. Do not edit `design.md` or `proposal.md` of this change except under a
  "## Measurement" heading at the end of `design.md` (see below).
- **Self-test.** `timeout 600 bash scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`
  from the root of the tree you are in; 75-90 s on the host; its summary line is
  `N passed, M failed` (grep for it; a traceback can be the last line). Baseline on this
  branch before task 1.1: `136 passed, 0 failed`. Run self-tests one after another, never
  two at once; run them directly, not under `setsid`; do not use `ps` (it is not in the
  loop image).
- **The launch mutant.** `sed -i '/^  FM_CHILD_PID=\$!$/i\  sleep 0.3 || :' scripts/benchmarking/feature_matrix/lib.sh`
  inserts one line before `FM_CHILD_PID=$!` and widens the launch window from microseconds
  to 0.3 s, so the signal checks land inside it. It is applied only in the scratch worktree
  of task 1.1 and is never committed. (The issue wrote `sleep 0.3` without `|| :`; design D4
  says why the guard is needed.)
- **If a total or a `not ok` line differs** from what a task says, STOP: do not commit, put
  the tree back (`git checkout -- scripts openspec`, and `git worktree remove --force /tmp/fm-mutant-824`
  if that worktree exists), write what you saw (the command, the totals, the `not ok`
  lines) under a "## Measurement" heading at the end of `design.md`, write the question in
  `docs/questions.md`, and exit.
- **Gate.** Run `bash scripts/gate.sh` in the foreground, with no pipe, no redirect and
  no `&`, and wait for its exit status. Never end a turn while it or `git commit` runs.
  The pre-commit hook runs the same gate; commit only through the hook, never with
  `--no-verify`. The gate runs the self-test too (its `N passed, 0 failed` line is in the
  gate output) and `diff-cover`, which measures only `src/`: a diff with no `src/` change
  prints "No lines with coverage information in this diff" and passes; that is expected.
- **TDD.** Each checkbox below is ONE commit with the suite green. The failing check and
  the code that makes it pass go in the SAME commit; put the red run's `not ok` lines and
  both totals in the commit body. Tick the box in the same commit as its work.
- Commit messages: short, lowercase, no `Co-Authored-By` trailer, no AI attribution. Use
  `git commit -F <file>` for a multi-line body, with the file OUTSIDE the repository
  (under `/tmp`).

## 1. Process a signal that arrives at the launch once the PID is known

- [x] 1.1 Red then green in one task (do not commit a red state). The window is
      microseconds wide, so the red run needs the launch mutant and a scratch worktree; the
      real tree gets the finished patch. From the repository root:
      (a) `git worktree add --detach /tmp/fm-mutant-824 HEAD`, then `cd /tmp/fm-mutant-824`.
      (b) Red: `git apply -p1 --include='scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh' openspec/changes/fix-issue-824-fm-launch-window/patches/A-task-1.1.diff`
      (adds header line 130, the stub's `qa-slow-selfterm` mode, the launcher
      `signal-launch-self.py` and check 130; design D3), then the launch mutant (the `sed`
      line above). Run the self-test and confirm EXACTLY `129 passed, 8 failed`, with these
      `not ok` lines: `qa_arm SIGTERM pid-only (121: launcher=-15 leftover:`, the same for
      `SIGHUP` (122, `launcher=-1 leftover`) and `SIGINT` (123, `launcher=-2 leftover`),
      `qa_arm sweep SIGTERM pid-only (launcher=-15 leftover:` (126), `qa_arm grace kill
      (launcher=-15 leftover:` (127), `qa_arm grace kill under a repeated group SIGTERM
      (launcher=-15 leftover:` (128), `qa_arm wrapper disposition during the grace period
      (launcher=-15 leftover caught:` (129), and `qa_arm self-signal at launch
      (launcher=-15 leftover:` (130). Checks 104, 105 and 112 (group signals) pass under the
      mutant: the group signal reaches the QA stub directly.
      (c) Green under the mutant: `git checkout -- scripts/benchmarking/feature_matrix/lib.sh`,
      then `git apply -p1 --exclude='scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh' openspec/changes/fix-issue-824-fm-launch-window/patches/A-task-1.1.diff`
      (adds `fm__deferred_signal`, `fm__arm_signal_traps`, the deferring traps, the re-arm
      and the processing site in `fm_run_interruptible`, and the comment paragraph; design
      D1-D2), then the launch mutant again. Run the self-test and confirm EXACTLY
      `137 passed, 0 failed`.
      (d) `cd` back to the repository root and run `git worktree remove --force /tmp/fm-mutant-824`;
      confirm `git worktree list` no longer shows it.
      (e) Real tree: `git apply -p1 openspec/changes/fix-issue-824-fm-launch-window/patches/A-task-1.1.diff`.
      Run the self-test and confirm EXACTLY `137 passed, 0 failed`. Confirm that
      `grep -c "fm__deferred_signal" scripts/benchmarking/feature_matrix/lib.sh` prints 7,
      that `grep -c "fm__arm_signal_traps" scripts/benchmarking/feature_matrix/lib.sh`
      prints 4, and that `git status --porcelain` lists only `lib.sh` and the self-test
      (and this `tasks.md`). (Loop 1 also measured the guard mutations of design D5; do
      not run them.)
      (f) Tick this box. Run `bash scripts/gate.sh` in the foreground and wait for exit 0.
      Commit the patch and the tick together only when the gate is green. Message first
      line: `fix(feature-matrix): close the signal window at the qa launch (#824)`; body:
      the red total and the eight `not ok` lines from (b), the green total from (c), the
      real-tree total from (e), and the line
      `mutant: sleep 0.3 || : before FM_CHILD_PID=$! in a scratch worktree, never committed`.

## 2. Docs

- [ ] 2.1 Apply patch B: `git apply -p1 openspec/changes/fix-issue-824-fm-launch-window/patches/B-task-2.1.diff`.
      It appends one paragraph, "**Follow-up (#824, the launch window).**", to the end of
      `openspec/changes/fix-issue-817-fm-wrapper-signal-during-qa/design.md` (issue plan
      step 4; the #817 change is not archived). Confirm that
      `grep -c "Follow-up (#824" openspec/changes/fix-issue-817-fm-wrapper-signal-during-qa/design.md`
      prints 1 (before the patch: 0) and that `git status --porcelain` lists only that file
      (and this `tasks.md`). Tick this box. Run `bash scripts/gate.sh` in the foreground and
      wait for exit 0. Commit only when the gate is green (message:
      `docs(openspec): note the #824 launch-window fix in the #817 design`).

## 3. Verify, push, and open the PR

- [ ] 3.1 Run the self-test and confirm `137 passed, 0 failed`. Run
      `git diff origin/dev...HEAD --stat` (three dots) and confirm it lists ONLY
      `scripts/benchmarking/feature_matrix/lib.sh`,
      `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`,
      `openspec/changes/fix-issue-817-fm-wrapper-signal-during-qa/design.md`, and files
      under `openspec/changes/fix-issue-824-fm-launch-window/`; remove any other file this
      branch added (nothing under `docs/`). If `command -v openspec` finds the CLI, run
      `openspec validate fix-issue-824-fm-launch-window --strict` and confirm it passes; if
      not, skip it (Loop 1 validated the change on the host). Tick this box, commit the tick
      through the hook (message: `mark #824 tasks complete`), then push and open the PR, so
      the push carries every commit. Push with
      `git push -u origin fix/issue-824-fm-launch-window`. Open the PR with
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-824-fm-launch-window`,
      the title `fix(feature-matrix): close the signal window at the qa launch (#824)`, and
      `Closes #824` in the BODY (not the title). Pass the body with a file under `/tmp`; do
      NOT commit a PR-body file to the repository. In the body: each acceptance criterion
      of #824 with its proof (self-test `137 passed, 0 failed`; the mutant red run
      `129 passed, 8 failed` and the mutant green run `137 passed, 0 failed`, both in the
      task 1.1 commit body; `grep -c fm__deferred_signal` prints 7; checks 121-129 pass
      unchanged; gate exit 0); the deviations from the issue text (proposal "Deviations":
      eight `not ok` lines under the mutant, not one; `sleep 0.3 || :` instead of
      `sleep 0.3`; the stub ignores TERM before it signals the wrapper; the scratch
      worktree); the guard mutations of design D5 with their failing checks; the design
      "Risks / Trade-offs"; and the note that `scripts/` has no coverage gate, so check 130
      and the mutant runs are the only proof. Then post one comment on PR #823
      (`gh pr comment 823 --repo fasrc/archi --body "<text>"`) that names the new PR and
      says it is the fix the round-4 comment deferred to #824. No `Co-Authored-By`
      trailer. Do not merge.
