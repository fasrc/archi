## ADDED Requirements

### Requirement: A signal to the wrapper's own PID during the QA run SHALL stop the run and page at once

`qa_arm.sh` MUST run each `archi qa` command (arm mode, and `qa run` and `qa score` in sweep mode) through `fm_run_interruptible`, which starts the command in the background with SIGINT at its default and waits for it with the interruptible `wait` builtin, so that a HUP, INT or TERM signal sent only to the wrapper's own PID is handled when it arrives and not when the command ends. On such a signal the wrapper MUST forward the same signal to the command, wait at most `FM_CHILD_GRACE` seconds (default 10) for it to end, send KILL if it is still there, then send exactly one page and end by that same signal, as the #624 requirement says; no process of the wrapper's process group MAY remain. The page body MUST name the command and its fate (stopped by the signal, or killed after the grace period). When the command exits non-zero without a signal, the wrapper MUST exit with that status and page exactly once, with the command and its status in the body. An empty `FM_PAGE_MAIL_TO` MUST still send no mail and MUST NOT change the exit status.

#### Scenario: SIGTERM to the wrapper's PID only during the QA run

- **WHEN** `FM_PAGE_MAIL_TO` is set and SIGTERM is sent to the PID of `qa_arm.sh` arm mode (not to its process group) while `archi qa` runs
- **THEN** within 5 seconds the wrapper dies of SIGTERM and the `archi qa` process is gone
- **AND** the mail binary receives exactly one message with `exit 143` in the subject and `stopped by SIGTERM` after the command in the body
- **AND** no ledger row is written

#### Scenario: SIGHUP and SIGINT to the wrapper's PID only

- **WHEN** the same signal test is run with SIGHUP, or with SIGINT
- **THEN** the wrapper dies of that signal within 5 seconds and the command is gone
- **AND** exactly one message carries `exit 129` (or `exit 130`) in the subject and the signal name in the body

#### Scenario: The QA command ignores SIGTERM

- **WHEN** `FM_PAGE_MAIL_TO` is set, `FM_CHILD_GRACE` is 1, and SIGTERM is sent to the wrapper's PID while an `archi qa` that ignores SIGTERM runs
- **THEN** within 5 seconds the command is killed and the wrapper dies of SIGTERM
- **AND** exactly one message says `killed after 1s (SIGTERM ignored)` after the command in the body

#### Scenario: The QA command exits 1

- **WHEN** `FM_PAGE_MAIL_TO` is set and `archi qa` exits 1 in `qa_arm.sh` arm mode
- **THEN** the wrapper exits 1
- **AND** the mail binary receives exactly one message with `exit 1` in the subject and the command followed by `exited 1` in the body
- **AND** no ledger row is written

#### Scenario: Paging off

- **WHEN** `FM_PAGE_MAIL_TO` is empty and `archi qa` exits 1 in `qa_arm.sh` arm mode
- **THEN** the mail binary is not run and the wrapper exits 1

#### Scenario: SIGTERM to the PID of qa_arm.sh --sweep during qa run

- **WHEN** `FM_PAGE_MAIL_TO` is set and SIGTERM is sent to the PID of `qa_arm.sh --sweep` while `archi qa run` runs
- **THEN** within 5 seconds the wrapper dies of SIGTERM
- **AND** exactly one message names the stack, the arm, `exit 143` and `stopped by SIGTERM`

#### Scenario: Group signals still page once

- **WHEN** `FM_PAGE_MAIL_TO` is set and the process group of `qa_arm.sh` arm mode receives SIGTERM, SIGHUP or SIGINT during the QA run
- **THEN** the wrapper dies of that signal and the mail binary receives exactly one message (the #624 checks 104, 105 and 112 keep passing)
