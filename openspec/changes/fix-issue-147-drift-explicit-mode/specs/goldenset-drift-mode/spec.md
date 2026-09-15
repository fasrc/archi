## ADDED Requirements

### Requirement: Explicit drift-mode selection

The goldenset maintenance tool's `drift` subcommand SHALL require the operator to explicitly
choose the drift pass to run: `--model <id>` selects the semantic pass (hash tripwire, then an
LLM verdict on each hash mismatch), and `--tripwire-only` selects the hash-only pass (the
tripwire alone, no LLM call). The two flags SHALL be mutually exclusive, and exactly one of
them SHALL be required. Invoking `drift` with neither flag, or with both, SHALL exit non-zero
without running any pass. The error for the neither-flag case MUST name **both** `--model` and
`--tripwire-only` so the operator can tell which mode they want. The hash-only pass reachable
via `--tripwire-only` SHALL make no LLM call and MUST remain available, because the read-only
dev-server cron depends on it.

#### Scenario: Neither mode flag is given

- **WHEN** `drift` is invoked with a bank and allowed-hosts but neither `--model` nor
  `--tripwire-only`
- **THEN** the command exits non-zero without running the tripwire or any LLM call
- **AND** the error message names both `--model` and `--tripwire-only`

#### Scenario: Tripwire-only mode runs without an LLM

- **WHEN** `drift` is invoked with `--tripwire-only`
- **THEN** the command runs the hash tripwire, reports any moved hash, and exits 0
- **AND** no LLM call is made for any drifted row

#### Scenario: Both mode flags are given

- **WHEN** `drift` is invoked with both `--model` and `--tripwire-only`
- **THEN** the command exits non-zero without running any pass, rejecting the contradictory
  instruction

### Requirement: Drift report header names the selected mode

A `drift` run SHALL state the selected mode in its report header, so a hash-only run declares
on its face that the semantic pass was not run rather than signalling it only by the absence of
verdicts.

#### Scenario: Tripwire-only header declares the mode

- **WHEN** a `drift --tripwire-only` run prints its report header
- **THEN** the header text states that the run is the hash-only / tripwire-only mode
