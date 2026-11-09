## ADDED Requirements

### Requirement: A signal that reaches the wrapper during the launch of the QA command SHALL be processed once the command's PID is known

`fm_run_interruptible` MUST record, and not act on, a HUP, INT or TERM signal that arrives between the start of its command and the record of the command's PID; once the PID is known it MUST re-arm the signal traps of `fm_arm_page_on_failure` and process a recorded signal through the same handler, so that the wrapper forwards the signal to the command, waits at most `FM_CHILD_GRACE` seconds, sends KILL if the command is still there, pages exactly once and ends by that signal. The command MUST NOT outlive the wrapper. The handling of a signal that arrives while the command runs (the #817 requirement) and of a signal to the whole process group (the #624 requirement) MUST NOT change.

#### Scenario: SIGTERM to the wrapper's PID as the QA command starts

- **WHEN** `FM_PAGE_MAIL_TO` is set, `FM_CHILD_GRACE` is 1, and the `archi qa` command of `qa_arm.sh` arm mode sends SIGTERM to the wrapper's PID as its first action and ignores SIGTERM itself
- **THEN** within 5 seconds the command is killed and the wrapper dies of SIGTERM, and no process of the wrapper's process group remains
- **AND** the mail binary receives exactly one message with `exit 143` in the subject and `killed after 1s (SIGTERM ignored)` after the command in the body
- **AND** no ledger row is written

#### Scenario: The signal lands inside the launch window

- **WHEN** the launch window is widened to 0.3 seconds by a `sleep 0.3 || :` between the start of the command and the record of its PID (the launch mutant, applied in a scratch worktree and never committed) and the signals of the PID-only and repeated-group checks (121-123, 126-130) land inside it
- **THEN** every one of those checks passes on the fixed code (`137 passed, 0 failed`)
- **AND** on the pre-fix code every one of them fails with the launcher output `leftover`: the wrapper paged with no PID to stop and the command outlived it (`129 passed, 8 failed`)

#### Scenario: Signals during the QA run are unchanged

- **WHEN** SIGTERM, SIGHUP or SIGINT reaches the wrapper's PID or its process group while the QA command runs (checks 104, 105, 112 and 121-129)
- **THEN** the #817 and #624 behavior holds unchanged, and those checks pass without edits
