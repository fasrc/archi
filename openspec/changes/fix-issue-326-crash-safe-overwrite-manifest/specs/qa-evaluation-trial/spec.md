## ADDED Requirements

### Requirement: The on-disk run manifest is always a document the schema accepts

Every `manifest.json` the QA-evaluation workflow writes SHALL round-trip through `RunManifest.from_dict` without raising, at every instant of every phase, including the overwrite paths of `run` and `score`.

A manifest write is a publish, and a publish is what a crash freezes. `run(overwrite=True)`
prunes the run and score phases, `attempts`, `agent`, and the RUN/SCORE digests
(`src/evaluation/qa/workflow.py:352-364`) and then publishes at `:380` with the prior terminal
status still in place; `score(overwrite=True)` does the same at `:796-803` with status
`scored`. Measured on `origin/dev` at `3de206bc`, the published document is refused by three
different rules depending on which status the run was in — the required-phases table
(`schema.py:258-267`), the `attempts` check (`:269-273`), and the qa-v2 live-check rule
(`:300-311`).

The window is not an instant. It spans the whole attempts phase for `run` and the whole scoring
phase for `score`, so any worker death — redeploy, OOM, SIGKILL, Ctrl-C on a CLI `--overwrite`
re-run — leaves the workspace in the refused state, and `run`, `score`, `retry_plan` and
history detail all raise on it afterwards.

The status published must be the status the surviving evidence proves, not the status the run
used to hold. That is the whole of the fix: the prune already decided the downstream phases no
longer exist, and the document has to say so.

The validator is not the thing that changes. Its rules are true statements about the pruned
document, and an implementation that widens them to admit the intermediate state also admits
genuinely corrupt manifests forever after.

#### Scenario: Every manifest published during a continued run is valid

- **WHEN** `run` is invoked with `overwrite=True` and `authorize_staged_invalid=True` on a
  paused run whose manifest status is `attention_required`
- **THEN** every `manifest.json` written during the call is accepted by `RunManifest.from_dict`
- **AND** that holds for the document published before the attempts phase begins, not only for
  the one left behind at the end
- **AND** the manifest the call returns still reports the real terminal status

#### Scenario: Every manifest published during an overwriting re-score is valid

- **WHEN** `score` is invoked with `overwrite=True` on a workspace whose manifest status is
  `scored`
- **THEN** every `manifest.json` written during the call is accepted by `RunManifest.from_dict`
- **AND** the document published before the scoring phase begins reports `run_completed`, the
  status its surviving prepare and run phases prove

#### Scenario: A worker death mid-overwrite leaves a workspace that still loads

- **WHEN** the process is killed after the overwrite prune has been published and before the
  phase completes
- **THEN** reading the workspace afterwards raises no schema error
- **AND** history lists the run under a real status rather than the `invalid` fallback row
  (`src/evaluation/qa/history.py:614`)

#### Scenario: The schema validator is not weakened

- **WHEN** the existing schema unit tests run against the change
- **THEN** they pass unmodified
- **AND** a manifest that claims a terminal status without the phases, `attempts`, or artifacts
  that status requires is still refused

This scenario is the fence around the other three. Making the writer honest and making the
validator permissive both turn the failing tests green, and only one of them is a fix.

### Requirement: Staged live-check evidence outlives the window that replaces it

A continued run SHALL keep the staged `live_checks.jsonl` and its recorded digest until the replacement artifact is committed, so no instant exists at which neither copy is available.

`run(overwrite=True, authorize_staged_invalid=True)` deliberately excludes
`live_checks.jsonl` from the files it removes (`workflow.py:355`), reads it to rebuild the
authorized-item set, and then unlinks it at `:414` — before the replacement is written at
`:463` or `:565`. The paused run's only copy of that evidence is gone for the length of the
attempts phase.

Keeping the file is not enough on its own. The prune drops the same name from
`manifest["artifacts"]`, and the continue precondition needs both halves: `required_inputs`
adds `live_checks.jsonl` when `authorize_staged_invalid` (`:308-309`) and `verify_hashes`
(`:315-319`) reads its digest out of the manifest. Evidence on disk that the manifest does not
record is evidence the next continue refuses to use.

The retention is keyed off the same flag as the file retention. A plain `--overwrite` that was
never asked to authorize staged items still discards the prior run's live checks at `:356`,
because discarding is what `--overwrite` means; widening this requirement to cover that path
would change the command's contract.

#### Scenario: An exception after the read leaves the evidence intact

- **WHEN** an exception is raised inside a continued run after the staged live checks have been
  read into the authorized set and before the replacement artifact is committed
- **THEN** `live_checks.jsonl` is still on disk with its original contents
- **AND** the last published manifest still records that file's digest
- **AND** a subsequent continue passes its input verification instead of failing on a missing
  artifact

#### Scenario: The replacement supersedes the staged copy without a gap

- **WHEN** a continued run completes its live checks and writes the replacement
  `live_checks.jsonl`
- **THEN** at no point during the write does the path hold a partial file or no file
- **AND** the manifest's recorded digest for it matches the committed contents once the phase
  publishes

#### Scenario: A plain overwrite still discards the prior live checks

- **WHEN** `run` is invoked with `overwrite=True` and `authorize_staged_invalid=False` on a
  workspace that has a `live_checks.jsonl`
- **THEN** that file is removed by the overwrite, as it is today
- **AND** its digest is removed from the manifest with it

This scenario is why the fix is conditional. "Never delete the staged checks" is the easy
over-correction, and it silently redefines `--overwrite` for every run that is not a continue.
