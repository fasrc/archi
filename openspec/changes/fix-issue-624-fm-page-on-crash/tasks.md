# Tasks — fix-issue-624-fm-page-on-crash

## 0. Read first (no checkbox)

- **Resume, do not re-plan.** You are on `fix/issue-624-fm-page-on-crash`. Loop 1 merged
  `origin/dev` `1ca97e24` into it and narrowed this change to paging only (the pin work
  was done by #661). Do not branch again from `dev`. Do not touch the pin code
  (`archive_run.sh:297-310`, `sweep_tools.py`).
- **Loop image gap (#776).** The `archi-loop` image has no `slack_sdk`. Without it, pytest
  stops at collection with 3 errors in `tests/unit/test_slack_*.py`, and the pre-commit
  hook refuses every commit. At the start of each turn, run `python -c "import slack_sdk"`.
  If it fails, run `pip install slack_sdk==3.45.0` (the pin in
  `requirements/requirements-base.txt`) before any test or gate run. Do not edit any
  requirements file for this.
- **Time box.** Run every self-test invocation under `timeout 600`:
  `timeout 600 bash scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`.
  It takes about 60 s on the host. If it exceeds 600 s, stop, and write which check hung
  (the last `ok`/`not ok` line it printed) in `docs/questions.md`.
- **Gate.** Run `bash scripts/gate.sh` in the foreground, with no pipe and no redirect.
  Never put it or `git commit` in the background, and never end a turn while either runs.
  The pre-commit hook runs the same gate; commit only through the hook, never with
  `--no-verify`.
- **Scope.** The design of record is `design.md` (D1-D7) in this folder. Edit only
  `scripts/benchmarking/feature_matrix/{lib.sh,archive_run.sh,test_feature_matrix_wrappers.sh}`,
  `docs/docs/proposals/feature-matrix-campaign-2026.md`, and this folder. In `lib.sh`,
  change only the lines that design D2 shows. Do not edit `qa_arm.sh`. Do not change an
  existing self-test check (1-101) except as a task says. Do not touch `deploy/`,
  `config/`, `.github/`, or any requirements file.
- **Self-test rules** (`scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`).
  Baseline after the merge: `108 passed, 0 failed`. The highest check number is 101
  (`dev` has 1-97; the branch's checks are 98-101). The new checks are 102-107. Add each
  to the header contract list (after the `#   101.` line), and put each check body
  directly above the final `printf '\n%d passed, %d failed\n'` line, in number order. The
  appendix at the end of this file has the exact check bodies; Loop 1 ran each of them,
  red and green, in a scratch copy of this branch. Copy them; do not rewrite them. The
  paging checks (60-72, 98-101) must stay green after every task. Do NOT write a second
  `$T/bin/python-ledger-fail` shim: reuse dev's shim and `REAL_PY` (above check 78).
- **TDD.** Each checkbox below is ONE commit with the suite green. The failing checks and
  the code that makes them pass go in the SAME commit; put the red run (the `not ok`
  lines and the totals) in the commit body. Tick the box in the same commit as its work.
- Commit messages: short, lowercase, no `Co-Authored-By` trailer. Use `git commit -F <file>`
  for a multi-line body, with the file OUTSIDE the repository.

## 1. The failure trap

- [x] 1.1 Red then green: add a stub mode in the self-test that fails the fingerprint read after `eval qa` (the `archi` stub touches `$T/fp-broken` when `$T/fp-fail-after-qa` exists, and the `docker` stub exits 1 on the fingerprint read while `$T/fp-broken` exists). Add check 78 (renumbered to 98 at the dev merge): with `FM_PAGE_MAIL_TO=ops@example.org`, `qa_arm.sh` arm mode exits non-zero, `$T/mail.calls` has exactly one `ARGS:` line that names `stack fm-00 arm 00`, and the ledger row count does not change. In `lib.sh`, initialize `FM_PAGED=0`, set `FM_PAGED=1` as the first statement of `fm_page`, and add `fm_arm_page_on_failure`, `fm__page_on_exit` and `fm__page_on_signal` (design D1, D2). Arm the trap in `qa_arm.sh` arm mode directly before the QA call (design D3). Done in `1be4177e`.
- [x] 1.2 Red then green: add checks 79-81 (renumbered to 99-101 at the dev merge): the fingerprint-read failure in `qa_arm.sh --sweep` pages exactly once; an `archi` stub mode that exits 1 on `qa score` makes `qa_arm.sh --sweep` exit non-zero and page exactly once; with `FM_PAGE_MAIL_TO` empty, that failure sends no mail and gives the same exit status. Arm the trap in `qa_arm.sh` sweep mode directly before `qa run` (design D3). Done in `8b6ae518`.
- [x] 1.3 Red then green: the page body says why (design D2, D5). Add checks 102 and 103 from appendix block A, and add their header lines (`#   102. a failed ledger append in qa_arm.sh arm mode pages once and names the failed command and its file:line`, `#   103. a QA run that scored nothing pages once with the refusal in the body`). Run the self-test and watch it fail: `108 passed, 2 failed` (102: the body says `at line 415`, not `at lib.sh:415`; 103: the body has no refusal text). Then change `lib.sh` exactly as design D2 shows, for these lines only: add `FM_DIE_MSG=""` after `FM_PAGED=0`; make `fm_die` set `FM_DIE_MSG="$*"` first; make the `ERR` trap record `fm__failed_line="${BASH_SOURCE[0]##*/}:$LINENO"`; and give `fm__page_on_exit` the `local body` / `if [ -n "$FM_DIE_MSG" ]` form. Update the comment above `fm_arm_page_on_failure` to say that the body carries the `fm_die` message or the failed command with its `file:line`. Self-test `110 passed, 0 failed`. Gate green, commit (`fix(#624): say in the failure page why the run stopped`).
- [x] 1.4 Red then green: a signal ends the wrapper as that signal (design D6, D7). In the `archi` stub, directly above the line `[ "\$1" = "qa" ] && [ -f "$T/drift-after-qa" ] ...`, add the line from appendix block B1. Add the launcher (block B2) and checks 104-105 (block B3), and add their header lines (`#   104. a SIGTERM to qa_arm.sh's process group during the QA run pages once, and the wrapper dies of SIGTERM`, `#   105. the same for SIGHUP`). Run the self-test and watch it fail: `110 passed, 2 failed`, with `launcher=143 clean` (104) and `launcher=129 clean` (105). Then change `fm_arm_page_on_failure`'s three signal traps and `fm__page_on_signal` exactly as design D2 shows (pass the signal name; `trap - EXIT "$2"`; `kill -s "$2" "$$"`; `exit "$1"` as the fallback), and update the comment above `fm_arm_page_on_failure` to say that a signal pages and then ends the wrapper by the same signal. Self-test `112 passed, 0 failed`. Do not use `setsid`, `timeout` around the wrapper, `ps`, or a fixed sleep before the signal (design D6). Gate green, commit (`fix(#624): end the wrapper by the signal that stopped it`).
- [x] 1.5 Red then green: the `archive_run.sh` arm points (design D3, D4). Add checks 106 and 107 from appendix block C, and add their header lines (`#   106. a failed ledger append in archive_run.sh arm mode pages once and leaves no pin`, `#   107. no artifact with paging on sends nothing (a precondition)`). Run the self-test and watch it fail: `113 passed, 1 failed` (106: `rc=1` and 0 pages; 107 passes). Then, in `archive_run.sh`, add `fm_arm_page_on_failure "stack $STACK arm $ARM"` on the line directly after the arm-mode `ARTIFACT="${FM_SELECTED%%...` line (`:105`), and `  fm_arm_page_on_failure "stack $STACK sweep"` (two-space indent) on the line directly after the sweep-mode `ARTIFACT=` line (`:68`). The sweep arm point has no check (design D3); do not look for one. Self-test `114 passed, 0 failed`. Show that check 107 guards the boundary: temporarily move the arm-mode line to directly above the `FM_SELECTED="$(fm_select_result "$STACK")" || fm_die "nothing archived ...` line (`:104`), run the self-test, and see `113 passed, 1 failed` (107 gets one page; measured in Loop 1); copy that line into the commit body, then put the line back. In the self-test, change the section comment above check 60 so that it also names the failure page: after long work starts, any other failure exit, and a SIGHUP, SIGINT or SIGTERM, pages once (#624). Gate green, commit (`fix(#624): page on any failure after archive_run.sh finds the artifact`).

## 2. Pin after ledger (removed)

Removed by the operator on 2026-10-20: #661 (`4e361f97`) and #696 (`c2e885ca`) already
write the pin after the ledger row. No task.

## 3. Docs

- [x] 3.1 In `docs/docs/proposals/feature-matrix-campaign-2026.md`, make 3 edits in the same plain style. (a) In the "**Paging (#504).**" paragraph, insert new sentences directly AFTER "The exit code (2) and the terminal message do not change." (about line 181), not before it, because that sentence is about refusals only. Say: after long work starts (the QA run in `qa_arm.sh`, the artifact found in `archive_run.sh`), any other failure exit also pages once ([#624](https://github.com/fasrc/archi/issues/624)); that page's subject ends with `failed after long work (exit N)` and keeps the exit status N; its body names the wrapper and the exit status, and then the refusal message, or else the `file:line` and the command that failed; a SIGHUP, SIGINT or SIGTERM to the wrapper also pages once, and the wrapper then ends by that signal, so a chain that runs it stops too; a refusal that already paged does not page again. (b) In the same paragraph, replace "comes after long work but does not page either; it exits 2 with no ledger row." (it spans lines 184-185) with a sentence that says this refusal comes after long work, so it pages once through that failure page, with the refusal in the body (#624), and that it still exits 2 with no ledger row. (c) In the `qa_arm.sh` step that reads `summary.json` (about line 294), replace "and does **not** page" with "and pages once (see Paging above)", and keep the #434 link. Then confirm `grep -n "not\*\* page\|page either" docs/docs/proposals/feature-matrix-campaign-2026.md` prints nothing (before the edits it prints lines 185 and 295). If `mkdocs` is installed, run `mkdocs build --config-file docs/mkdocs.yml --strict --site-dir /tmp/mkdocs-624` and confirm it prints no WARNING; if it is not installed, say so in the commit body. Gate green, commit (`docs(#624): name the failure page in the feature-matrix runbook`).

## 3b. Post-PR verification findings (wrap-up, 2026-10-25)

The ultracode check over PR #782 at `1687614b` confirmed 3 gaps in the test proof. The code
at the head is correct; each gap lets a later regression pass the gate. Do these 3 boxes in
order (3.2, 3.3, 3.4), before 4.1. Wrap-up ran each block below in a scratch copy of
`1687614b`: with all three tasks applied, `119 passed, 0 failed` (also with the self-test
run under `trap '' INT`), and each red step below failed exactly the named checks. Each
red step is a TEMPORARY change to `qa_arm.sh`, `archive_run.sh` or `lib.sh`: put it back
before you commit, and confirm that `git diff -- scripts/benchmarking/feature_matrix/qa_arm.sh scripts/benchmarking/feature_matrix/archive_run.sh scripts/benchmarking/feature_matrix/lib.sh`
prints nothing. Section 0's "Do not edit `qa_arm.sh`" still holds for the commit.

- [x] 3.2 Red then green: a precondition before each arm point never pages (spec: "A refusal before the arm point MUST NOT page"; finding 1). Before this task, only checks 64 and 107 cover this, both in `archive_run.sh` arm mode. Add checks 108-111 from appendix block D directly above the final `printf '\n%d passed, %d failed\n'` line, and add their 4 header lines from block D (108-111 only) after the `#   107.` line. Self-test `118 passed, 0 failed`. Then show that each new check guards its arm point, one at a time, and put each line back: (a) in `qa_arm.sh`, move `fm_arm_page_on_failure "stack $STACK arm $ARM"` to directly above the arm-mode line `[ ! -e "$OUT_DIR" ] || fm_die "output dir exists: ...` → `117 passed, 1 failed` (109); (b) move `  fm_arm_page_on_failure "stack $STACK arm $STEM"` to directly above `  [ -n "$SPEC" ] || fm_die "arm '$STEM' is not in the sweep lock $LOCK"` → `117 passed, 1 failed` (110); (c) in `archive_run.sh`, move `  fm_arm_page_on_failure "stack $STACK sweep"` to directly above `  fm_require_sweep_lock "$STACK" --stamped` → `117 passed, 1 failed` (111). Copy the three `not ok` lines into the commit body. Gate green, commit (`test(#624): prove that no precondition pages, at every arm point`).
- [ ] 3.3 Red then green: checks 102 and 106 pin the line number (spec scenario "Ledger append fails in qa_arm": the body names `lib.sh`, the line, and the command; design D2; finding 2). Now `lib.sh:[0-9]*:` also matches an empty line number or the caller's line. Directly above the `# 102:` comment line, add the 2 lines of appendix block E1. In check 102, replace the line `   && grep -q "qa_arm.sh exited 1 at lib.sh:[0-9]*: FM_LEDGER=" "$T/mail.calls" \` with the first line of block E2; in check 106, replace `   && grep -q "archive_run.sh exited 1 at lib.sh:[0-9]*: FM_LEDGER=" "$T/mail.calls" \` with its second line. Self-test `118 passed, 0 failed`. Red: temporarily change the `ERR` trap in `lib.sh` from `fm__failed_line="${BASH_SOURCE[0]##*/}:$LINENO"` to `fm__failed_line="${BASH_SOURCE[0]##*/}:${BASH_LINENO[0]}"` → `116 passed, 2 failed` (102 and 106; before this task the same change gave `114 passed, 0 failed`). Put it back. Gate green, commit (`test(#624): pin the lib.sh line in the failure page checks`).
- [ ] 3.4 Red then green: a SIGINT check (spec requirement: a SIGHUP, SIGINT or SIGTERM MUST page once and end the wrapper by that signal; finding 3). In the launcher heredoc (`cat > "$T/bin/signal-launch.py"`), replace the `p = subprocess.Popen(...)` line with the 2 lines of appendix block F1: the child starts with SIGINT at its default, so the check holds even when the self-test runs with SIGINT ignored. In the 104-105 loop, replace `for SIGCASE in "104 TERM 143 -15" "105 HUP 129 -1"; do` with block F2. Add the `#   112.` header line from block D after the `#   111.` line. Self-test `119 passed, 0 failed`. Red: temporarily delete the line `  trap 'fm__page_on_signal 130 INT' INT` in `lib.sh` → `118 passed, 1 failed` with `not ok - qa_arm SIGINT page (112: launcher=-2 clean: )`. Put it back. In `design.md` D6, replace the sentence "SIGINT has no check: a background job of a non-interactive shell starts with SIGINT ignored, and bash cannot trap a signal that was ignored at entry." with: "Check 112 sends SIGINT the same way. A background job of a non-interactive shell starts with SIGINT ignored, and bash cannot trap a signal that was ignored at entry, so the launcher sets SIGINT to the default in the child (`preexec_fn`). Measured: 119 passed with the self-test run under `trap '' INT`." Run `openspec validate fix-issue-624-fm-page-on-crash --strict` if the CLI is there. Gate green, commit (`test(#624): check that a SIGINT pages and ends the wrapper`).

## 4. Verify, push, and open the PR

- [ ] 4.1 Run `timeout 600 bash scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh` and confirm `119 passed, 0 failed`. Run `bash scripts/gate.sh` on the branch tip and confirm it is green. If `command -v openspec` finds the CLI, run `openspec validate fix-issue-624-fm-page-on-crash --strict` and confirm it passes; if not, skip it (Loop 1 validated the change on the host). Run `git diff origin/dev...HEAD --stat -- docs/` (three dots: only this branch's own changes) and confirm it lists ONLY `docs/docs/proposals/feature-matrix-campaign-2026.md`; remove any other file under `docs/` that this branch added or changed. Tick this box, commit the tick through the hook, then confirm `git status --porcelain` is empty. Then push with `git push -u origin fix/issue-624-fm-page-on-crash`. PR #782 is already open on this branch (the harness opened it), so do NOT run `gh pr create`. Write the PR body to a file OUTSIDE the repository (for example `/tmp/pr-body-624.md`; never under `docs/`), then run `gh pr edit 782 --repo fasrc/archi --title "fix(feature-matrix): page on any failure after long work (#624)" --body-file /tmp/pr-body-624.md`. In the PR **body** (not the title) put `Closes #624` on its own line; list each acceptance criterion of #624 with the check that proves it (98-112, plus the existing paging checks 60-72); give the self-test count (67 at `7730d086`, 108 after the dev merge, 119 at the tip); state the scope change (the pin work was done by #661, so this PR is paging only); state the behavior changes (the "scored nothing" refusal now pages, design D5; a signal to the wrapper pages and then ends it by that signal, design D7; the page body carries the refusal or the failed `file:line` and command, design D2); state the deviations from the issue text (no stderr capture, the `fm_die` message instead, design D2; a Python launcher instead of `timeout` for the signal checks, design D6; the sweep-mode `archive_run.sh` arm point has no failure check, only the precondition check 111, design D3); list design "Risks / Trade-offs"; and list the 3 post-PR verification findings with the task that fixed each (3.2-3.4). No `Co-Authored-By` trailer. Do not merge.

## Appendix: check bodies (run red and green by Loop 1)

Copy each block as it is. Blocks B1-B3 depend on each other; block C depends on dev's
`python-ledger-fail` shim, `REAL_PY`, `run`, `artifact` and `ledger_rows`.

### A. Checks 102-103 (task 1.3)

```bash
# 102: a failed ledger append in qa_arm.sh arm mode pages once, and the body names the failed command and its file:line
: > "$T/mail.calls"; BEFORE="$(ledger_rows)"
FM_PYTHON="$T/bin/python-ledger-fail"
run env FM_AGENT_SPEC="$T/cfg/spec.md" FM_PAGE_MAIL_TO=ops@example.org bash "$HERE/qa_arm.sh" 00 "$T/arms/00-baseline.yaml" --profile "$T/cfg/qa/profile.yaml"
FM_PYTHON="$REAL_PY"
if [ "$RC" = 1 ] && [ "$(grep -c '^ARGS:' "$T/mail.calls")" = 1 ] \
   && grep -q "ARGS: -s feature_matrix: stack fm-00 arm 00: failed after long work (exit 1)" "$T/mail.calls" \
   && grep -q "qa_arm.sh exited 1 at lib.sh:[0-9]*: FM_LEDGER=" "$T/mail.calls" \
   && [ "$(ledger_rows)" = "$BEFORE" ]; then
  ok "a failed ledger append in qa_arm.sh arm mode pages once and names the failed command"; else notok "qa_arm ledger-fail page (rc=$RC: $(cat "$T/mail.calls" "$T/stderr"))"; fi

# 103: a QA run that scored nothing pages once through the failure trap, and the body carries the refusal
printf '{"attempt_lifecycle_counts": {"scored": 0, "execution_failed": 0, "evaluation_failed": 0}}' > "$T/qa-summary"
: > "$T/mail.calls"; BEFORE="$(ledger_rows)"
run env FM_AGENT_SPEC="$T/cfg/spec.md" FM_PAGE_MAIL_TO=ops@example.org bash "$HERE/qa_arm.sh" 00 "$T/arms/00-baseline.yaml" --profile "$T/cfg/qa/profile.yaml"
rm -f "$T/qa-summary"
if [ "$RC" = 2 ] && grep -q 'QA run scored nothing' "$T/stderr" && [ "$(grep -c '^ARGS:' "$T/mail.calls")" = 1 ] \
   && grep -q "ARGS: -s feature_matrix: stack fm-00 arm 00: failed after long work (exit 2)" "$T/mail.calls" \
   && grep -q "qa_arm.sh exited 2: feature_matrix: QA run scored nothing" "$T/mail.calls" \
   && [ "$(ledger_rows)" = "$BEFORE" ]; then
  ok "a QA run that scored nothing pages once with the refusal in the body"; else notok "qa_arm scored-0 page (rc=$RC: $(cat "$T/mail.calls" "$T/stderr"))"; fi
```

### B1. `archi` stub slow mode (task 1.4; one line inside the stub heredoc)

```bash
[ "\$1" = "qa" ] && [ -f "$T/qa-slow" ] && { touch "$T/qa-started"; sleep 30; }
```

### B2. Signal launcher (task 1.4)

```bash
# signal launcher (checks 104-105): starts the command in its own session (its own process
# group), waits for the ready file, signals the whole group, and prints the wait status
# (negative = killed by that signal) and whether any process of the group was left.
cat > "$T/bin/signal-launch.py" <<'PY'
import os, signal, subprocess, sys, time
signame, ready, out, err, cmd = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]
p = subprocess.Popen(cmd, start_new_session=True, stdout=open(out, "w"), stderr=open(err, "w"))
deadline = time.monotonic() + 30
while not os.path.exists(ready) and p.poll() is None and time.monotonic() < deadline:
    time.sleep(0.05)
if p.poll() is None:
    os.killpg(p.pid, getattr(signal, "SIG" + signame))
try:
    rc = p.wait(timeout=30)
except subprocess.TimeoutExpired:
    rc = "hung"
try:
    os.killpg(p.pid, signal.SIGKILL)
    left = "leftover"
except ProcessLookupError:
    left = "clean"
print(rc, left)
PY
```

### B3. Checks 104-105 (task 1.4)

```bash
# 104-105: a SIGTERM or SIGHUP to qa_arm.sh arm mode during the QA run pages once, and the
# wrapper then dies of that signal (so a calling shell sees a signal death, not an exit)
for SIGCASE in "104 TERM 143 -15" "105 HUP 129 -1"; do
  read -r N SIG CODE WANT <<<"$SIGCASE"
  touch "$T/qa-slow"; rm -f "$T/qa-started"
  : > "$T/mail.calls"; BEFORE="$(ledger_rows)"
  OUT="$(env FM_AGENT_SPEC="$T/cfg/spec.md" FM_PAGE_MAIL_TO=ops@example.org "$REAL_PY" "$T/bin/signal-launch.py" "$SIG" "$T/qa-started" "$T/stdout" "$T/stderr" bash "$HERE/qa_arm.sh" 00 "$T/arms/00-baseline.yaml" --profile "$T/cfg/qa/profile.yaml")"
  rm -f "$T/qa-slow" "$T/qa-started"
  if [ "$OUT" = "$WANT clean" ] && [ "$(grep -c '^ARGS:' "$T/mail.calls")" = 1 ] \
     && grep -q "ARGS: -s feature_matrix: stack fm-00 arm 00: failed after long work (exit $CODE)" "$T/mail.calls" \
     && [ "$(ledger_rows)" = "$BEFORE" ]; then
    ok "a SIG$SIG during the QA run pages once and the wrapper dies of SIG$SIG ($N)"; else notok "qa_arm SIG$SIG page ($N: launcher=$OUT: $(cat "$T/mail.calls" "$T/stderr"))"; fi
done
```

### C. Checks 106-107 (task 1.5)

```bash
# 106: a failed ledger append in archive_run.sh arm mode pages once and leaves no pin
mkdir -p "$ARCHI_DIR/archi-fm-pin624"
run env RAGAS_ENV_FILE="$T/judge.env" bash "$HERE/run_arm.sh" 00 "$T/arms/00-baseline.yaml" --stack fm-pin624
artifact "$FM_OUT/benchmarking-fm-pin624-20260903_000040.json" '[]' pin624 5
: > "$T/mail.calls"; BEFORE="$(ledger_rows)"
FM_PYTHON="$T/bin/python-ledger-fail"
run env FM_PAGE_MAIL_TO=ops@example.org bash "$HERE/archive_run.sh" 00 1 "$T/arms/00-baseline.yaml" --stack fm-pin624
FM_PYTHON="$REAL_PY"
if [ "$RC" = 1 ] && [ "$(grep -c '^ARGS:' "$T/mail.calls")" = 1 ] \
   && grep -q "ARGS: -s feature_matrix: stack fm-pin624 arm 00: failed after long work (exit 1)" "$T/mail.calls" \
   && grep -q "archive_run.sh exited 1 at lib.sh:[0-9]*: FM_LEDGER=" "$T/mail.calls" \
   && [ ! -e "$FM_OUT/corpus-pin-fm-pin624" ] && [ "$(ledger_rows)" = "$BEFORE" ]; then
  ok "a failed ledger append in archive_run.sh pages once and leaves no pin"; else notok "archive ledger-fail page (rc=$RC: $(cat "$T/mail.calls" "$T/stderr"))"; fi

# 107: "nothing archived" (no artifact) with paging on still sends nothing: it is a precondition
mkdir -p "$ARCHI_DIR/archi-fm-noart"
run env RAGAS_ENV_FILE="$T/judge.env" bash "$HERE/run_arm.sh" 00 "$T/arms/00-baseline.yaml" --stack fm-noart
: > "$T/mail.calls"
run env FM_PAGE_MAIL_TO=ops@example.org bash "$HERE/archive_run.sh" 00 1 "$T/arms/00-baseline.yaml" --stack fm-noart
if [ "$RC" = 2 ] && grep -q "nothing archived for stack fm-noart" "$T/stderr" && [ ! -s "$T/mail.calls" ]; then
  ok "no artifact with paging on sends nothing"; else notok "no-artifact no-page (rc=$RC: $(cat "$T/mail.calls" "$T/stderr"))"; fi
```

### D. Checks 108-111 and the header lines 108-112 (tasks 3.2 and 3.4)

Header lines (108-111 in task 3.2; 112 in task 3.4):

```bash
#   108. a missing agent spec in qa_arm.sh arm mode with paging on sends nothing (a precondition)
#   109. an existing output dir in qa_arm.sh arm mode with paging on sends nothing (the last precondition)
#   110. an arm missing from the sweep lock in qa_arm.sh --sweep with paging on sends nothing
#   111. archive_run.sh --sweep with no sweep lock and paging on sends nothing
#   112. a SIGINT to qa_arm.sh's process group during the QA run pages once, and the wrapper dies of SIGINT (in the 104-105 loop)
```

Check bodies (task 3.2):

```bash
# 108: a precondition refusal in qa_arm.sh arm mode (agent spec not found) with paging on sends nothing
: > "$T/mail.calls"
run env FM_AGENT_SPEC="$T/cfg/no-such-spec.md" FM_PAGE_MAIL_TO=ops@example.org bash "$HERE/qa_arm.sh" 00 "$T/arms/00-baseline.yaml" --profile "$T/cfg/qa/profile.yaml"
if [ "$RC" = 2 ] && grep -q "agent spec not found" "$T/stderr" && [ ! -s "$T/mail.calls" ]; then
  ok "a missing agent spec in qa_arm.sh with paging on sends nothing"; else notok "qa_arm missing-spec no-page (rc=$RC: $(cat "$T/mail.calls" "$T/stderr"))"; fi

# 109: the last precondition before the qa_arm.sh arm point (output dir exists) with paging on sends nothing
: > "$T/mail.calls"
run env FM_AGENT_SPEC="$T/cfg/spec.md" FM_PAGE_MAIL_TO=ops@example.org bash "$HERE/qa_arm.sh" 00 "$T/arms/00-baseline.yaml" --profile "$T/cfg/qa/profile.yaml" --run 1
if [ "$RC" = 2 ] && grep -q "output dir exists" "$T/stderr" && [ ! -s "$T/mail.calls" ]; then
  ok "an existing output dir in qa_arm.sh with paging on sends nothing"; else notok "qa_arm outdir-exists no-page (rc=$RC: $(cat "$T/mail.calls" "$T/stderr"))"; fi

# 110: an arm missing from the sweep lock in qa_arm.sh --sweep with paging on sends nothing
: > "$T/mail.calls"
run env FM_PAGE_MAIL_TO=ops@example.org bash "$HERE/qa_arm.sh" --sweep "$SW/configs" --stack r0 --arm no-such-arm
if [ "$RC" = 2 ] && grep -q "not in the sweep lock" "$T/stderr" && [ ! -s "$T/mail.calls" ]; then
  ok "an arm missing from the sweep lock with paging on sends nothing"; else notok "qa_arm sweep bad-arm no-page (rc=$RC: $(cat "$T/mail.calls" "$T/stderr"))"; fi

# 111: archive_run.sh --sweep on a stack with no sweep lock, with paging on, sends nothing
: > "$T/mail.calls"
run env FM_PAGE_MAIL_TO=ops@example.org bash "$HERE/archive_run.sh" --sweep "$SW/configs" --stack r0-noart --run 1
if [ "$RC" = 2 ] && grep -q "no sweep lock" "$T/stderr" && [ ! -s "$T/mail.calls" ]; then
  ok "archive_run.sh --sweep with no sweep lock and paging on sends nothing"; else notok "archive sweep no-lock no-page (rc=$RC: $(cat "$T/mail.calls" "$T/stderr"))"; fi
```

### E. Line-number pin (task 3.3)

E1 (directly above the `# 102:` comment line):

```bash
# The line of the ledger-append command in lib.sh; checks 102 and 106 expect it in the page body.
LEDGER_LINE="$(grep -n 'FM_LEDGER="\$ledger" FM_ENTRY' "$HERE/lib.sh" | cut -d: -f1)"
```

E2 (line 1 replaces check 102's `lib.sh:[0-9]*:` line; line 2 replaces check 106's):

```bash
   && [[ "$LEDGER_LINE" =~ ^[0-9]+$ ]] && grep -q "qa_arm.sh exited 1 at lib.sh:$LEDGER_LINE: FM_LEDGER=" "$T/mail.calls" \
   && [[ "$LEDGER_LINE" =~ ^[0-9]+$ ]] && grep -q "archive_run.sh exited 1 at lib.sh:$LEDGER_LINE: FM_LEDGER=" "$T/mail.calls" \
```

### F. SIGINT (task 3.4)

F1 (replaces the `p = subprocess.Popen(...)` line in the launcher heredoc):

```python
p = subprocess.Popen(cmd, start_new_session=True, stdout=open(out, "w"), stderr=open(err, "w"),
                     preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
```

F2 (replaces the 104-105 loop's `for SIGCASE ...` line):

```bash
for SIGCASE in "104 TERM 143 -15" "105 HUP 129 -1" "112 INT 130 -2"; do
```
