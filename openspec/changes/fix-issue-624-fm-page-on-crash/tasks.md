# Tasks — fix-issue-624-fm-page-on-crash

TDD in every task: write the failing check first, run it and watch it fail, then write
the minimum code. Each task ends green and ends with one commit through the gate
(`bash scripts/gate.sh` via the pre-commit hook; never `--no-verify`). Each task holds its
red check AND its fix, so no task ends red.

Self-test rules (`scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`):
checks 1-77 already exist (the header list ends at 77; baseline `84 passed, 0 failed` on
`6156b760`, re-measure before task 1.1). Number the new checks from 78 up, add each one
to the header contract list, and put each check body above the final
`printf '\n%d passed, %d failed\n'` line, never after the file's last line. "The paging
checks" below means checks 60-72; each must still see exactly one page (or none, for 61
and 64) after every task. In `tests/unit/test_sweep_tools.py`, run
`grep -n "def test_" tests/unit/test_sweep_tools.py` before you add a test name.

## 1. The failure trap

- [x] 1.1 Red then green: add a stub mode in the self-test that fails the fingerprint read after `eval qa` (for example, the `archi` stub touches `$T/fp-broken` when `$T/fp-fail-after-qa` exists, and the `docker` stub exits 1 on the fingerprint read while `$T/fp-broken` exists; remove both files after the check). Add check 78: with `FM_PAGE_MAIL_TO=ops@example.org`, `qa_arm.sh` arm mode exits non-zero, `$T/mail.calls` has exactly one `ARGS:` line that names `stack fm-00 arm 00`, and the ledger row count does not change. Watch it fail. Then, in `lib.sh`, initialize `FM_PAGED=0`, set `FM_PAGED=1` as the first statement of `fm_page`, and add `fm_arm_page_on_failure`, `fm__page_on_exit` and `fm__page_on_signal` exactly as design D1 and D2 show (with `set -E`). Arm the trap in `qa_arm.sh` arm mode directly before the `eval qa` call (design D3). Run the whole self-test: the paging checks must still pass. Commit.
- [x] 1.2 Red then green: add check 79: the same fingerprint-read failure in `qa_arm.sh --sweep` pages exactly once. Add check 80: an `archi` stub mode that exits 1 on `eval qa score` makes `qa_arm.sh --sweep` exit non-zero and page exactly once. Add check 81: with `FM_PAGE_MAIL_TO` empty, the check 80 failure sends no mail and gives the same exit status. Watch 79 and 80 fail. Then arm the trap in `qa_arm.sh` sweep mode directly before `eval qa run` (design D3). Commit.
- [ ] 1.3 Red then green: add an `archi` stub mode that sleeps 3 seconds in `eval qa` when `$T/qa-slow` exists. Add check 82: with paging on and `$T/qa-slow`, start `qa_arm.sh` arm mode in the background, wait 1 second, send `kill -TERM` to its process group (start it with `setsid` so it has its own group; bash defers a signal trap until the foreground child exits, and a group kill ends the child too), then `wait` for it. Expect exit status 143 and exactly one `ARGS:` line. Watch it fail (before the signal traps exist the status is 143 with 0 pages; if 1.1 already added `fm__page_on_signal`, this check passes at once: then say so in the commit message and keep it as a regression check). Commit.
- [ ] 1.4 Red then green: add a `$T/bin/python-ledger-fail` wrapper (design D6: exit 1 when both `FM_LEDGER` and `FM_ENTRY` are set, else `exec python3 "$@"`). Set up the new stack `fm-pin624` the way check 73 sets up `fm-tags`: `mkdir -p "$ARCHI_DIR/archi-fm-pin624"`, `run_arm.sh 00 <arm.yaml> --stack fm-pin624` for the ragas-start row and the stack lock, then a valid artifact `benchmarking-fm-pin624-<timestamp>.json` (`TAGS=true artifact ... '[]' def 5`). Do NOT reuse `fm-tags` (design D6). Add check 83: with `FM_PYTHON="$T/bin/python-ledger-fail" FM_PAGE_MAIL_TO=ops@example.org`, `archive_run.sh 00 1 <arm.yaml> --stack fm-pin624` exits non-zero and `$T/mail.calls` has exactly one `ARGS:` line naming `stack fm-pin624 arm 00`. Do not assert on the pin here. Watch it fail. Then arm the trap in `archive_run.sh` arm mode directly after the "no artifact" check, and in sweep mode directly after its "no artifact" check (design D3). The sweep-mode arm point is a backstop: after it, every sweep failure already pages through `fm_die_paged`, so no check can fail first for it. Do not look for one. Commit.

## 2. Pin after ledger

- [ ] 2.1 Red then green: extend check 83 (or add check 84 with the same setup, run after 83) to assert that no `corpus-pin-fm-pin624` file exists after the failed ledger write. Add check 85, run after it: the same archive without the wrapper exits 0 and writes `corpus-pin-fm-pin624` equal to the new ledger row's `corpus_fingerprint`. Watch the no-pin assertion fail. Then remove both `open(pin_file, "w")` writes from the validator in `archive_run.sh` and write the pin in bash after `fm_ledger_append "$ENTRY"` (design D4: only when the pin file is absent or `repinned_from` is not null; write `<pin>.tmp`, then `mv`; the helper that reads `ENTRY` must not set `FM_LEDGER`). Run the whole self-test: every existing re-pin and `--new-corpus` check (for example 59) must still pass. Commit.
- [ ] 2.2 Red then green: in `tests/unit/test_sweep_tools.py`, change the existing `test_a_retry_after_a_failed_ledger_write_completes` (about line 429). It pins today's order: its docstring says "Pins and copies land before the ledger replace" and it asserts `(sweep / "out/corpus-pin-r0").exists()` after the failed replace. Invert that assertion to assert that neither `corpus-pin-r0` nor the category-map pin for `r0` exists, and update the docstring to "Copies land before the ledger replace, pins after it; a retry of run 1 finishes." Keep the retry half unchanged. Do not add a second test with the same setup. Watch it fail. Then move the `if run == 1:` pin block in `sweep_tools.py archive` to after `os.replace(tmp, ledger)` (design D5). Run `python -m pytest -q tests/unit/test_sweep_tools.py`. Commit.

## 3. Docs

- [ ] 3.1 In `docs/docs/proposals/feature-matrix-campaign-2026.md` §5, extend the "**Paging (#504).**" paragraph: after long work starts (the QA run in `qa_arm.sh`, the artifact found in `archive_run.sh`), any other failure exit also pages once, and so does a SIGHUP, SIGINT or SIGTERM to the wrapper; the page carries the exit status and, when bash records them, the line and the failed command; a refusal that already paged does not page again; and a pin is written only after its ledger row (#624). Use the same plain style as the paragraph. Commit.

## 4. Publish

- [ ] 4.1 Push the branch (`git push -u origin fix/issue-624-fm-page-on-crash`) and open the PR: `gh pr create --repo fasrc/archi --base dev --head fix/issue-624-fm-page-on-crash`, title `fix(feature-matrix): page on any failure after long work; pin after ledger (#624)`, body with `Closes #624` on its own line, the self-test count before and after, the sweep-mode pin fix (design D5) and the signal traps (design D2) noted as beyond the issue's named lines but inside its scope, and the stderr-capture deviation (design D2).
