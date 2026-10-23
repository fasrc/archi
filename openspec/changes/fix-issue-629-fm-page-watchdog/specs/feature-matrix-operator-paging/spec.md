## ADDED Requirements

### Requirement: fm_page SHALL bound the mail command on a host without timeout when sleep is available

When `timeout` is not on `PATH` and `sleep` is on `PATH`, `fm_page` MUST send SIGTERM to the mail command after `FM_MAIL_TIMEOUT` seconds (after the existing validation of that value) and SIGKILL 5 seconds later if the mail command is still running. A mail command that is killed, exits non-zero, or is still running at the deadline (even if it then handles SIGTERM and exits 0) MUST log `page failed` to stderr. A fractional `FM_MAIL_TIMEOUT` MUST still bound the mail command when `sleep` accepts only whole seconds (POSIX); the deadline is rounded up to the next whole second. `fm_page` MUST return 0 in all cases, also when the caller runs under `set -euo pipefail`. When the mail command returns before the deadline, no process that `fm_page` started (the watchdog or its `sleep`) MUST remain after `fm_page` returns. When `timeout` and `sleep` are both absent, `fm_page` MUST still run the mail command, unbounded, and a successful mail command MUST NOT log `page failed`. The path with `timeout` on `PATH` MUST NOT change.

#### Scenario: Hung mail command is cut off without timeout

- **WHEN** `timeout` is not on `PATH`, `sleep` is on `PATH`, `FM_MAIL_TIMEOUT=1`, and the mail command sleeps 30 seconds
- **THEN** `fm_page` returns 0 in under 15 seconds
- **AND** stderr contains `page failed`

#### Scenario: Hung mail command that ignores SIGTERM is killed without timeout

- **WHEN** `timeout` is not on `PATH`, `sleep` is on `PATH`, `FM_MAIL_TIMEOUT=1`, and the mail command ignores SIGTERM and sleeps 30 seconds
- **THEN** `fm_page` returns 0 in under 15 seconds
- **AND** stderr contains `page failed`

#### Scenario: Fast mail command delivers and leaves no watchdog

- **WHEN** `timeout` is not on `PATH`, `sleep` is on `PATH`, `FM_MAIL_TIMEOUT=20`, the mail command returns at once, and the caller captures stderr through a pipe
- **THEN** the capture ends in under 10 seconds
- **AND** the mail command received the page
- **AND** stderr does not contain `page failed`

#### Scenario: Hung mail command that exits 0 on SIGTERM still fails the page

- **WHEN** `timeout` is not on `PATH`, `sleep` is on `PATH`, `FM_MAIL_TIMEOUT=1`, and the mail command sleeps 30 seconds but exits 0 on SIGTERM
- **THEN** `fm_page` returns 0 in under 15 seconds
- **AND** stderr contains `page failed`

#### Scenario: Fractional timeout with an integer-only sleep

- **WHEN** `timeout` is not on `PATH`, the `sleep` on `PATH` rejects a non-integer operand, `FM_MAIL_TIMEOUT=1.5`, and the mail command sleeps 30 seconds
- **THEN** `fm_page` returns 0 in under 15 seconds
- **AND** stderr contains `page failed`

#### Scenario: Failing mail command without timeout

- **WHEN** `timeout` is not on `PATH`, `sleep` is on `PATH`, and the mail command exits 1
- **THEN** `fm_page` returns 0 and stderr contains `page failed`

#### Scenario: Neither timeout nor sleep on PATH

- **WHEN** `PATH` holds only `env`, `bash` and `cat`, and the mail command returns at once
- **THEN** the mail command received the page and stderr does not contain `page failed`
