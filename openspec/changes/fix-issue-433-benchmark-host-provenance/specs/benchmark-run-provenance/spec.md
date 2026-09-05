## ADDED Requirements

### Requirement: The deploy records the host in `git_info.yaml`

`archi create` SHALL record the machine it runs on in `git_info.yaml`, as a `host` block holding a `hostname` and a `cpu_model`.

The capture belongs at deploy time, on the host. A hostname read inside the benchmark
container returns the container id, which is different on every run and identifies nothing.
`get_git_information()` (`src/cli/managers/templates_manager.py:79`) already builds the dict
that the call site at `:750-756` writes to `<base_dir>/git_info.yaml`, so the block rides an
existing channel and adds no new write path.

Capture SHALL never raise and SHALL never fail a deploy. Provenance is worth recording, and
it is never worth aborting a deploy for.

The two fields fail differently, and the difference is normative. An unreadable **processor
model** gives `None` for that key alone, so a machine that hides its model still reports its
hostname. An unreadable **hostname** gives `None` for the **whole block**, because a block
that names no machine identifies nothing and would otherwise create a fourth state that
every consumer below would read as a recorded host named `None`.

The block SHALL carry the hostname and the processor model only. No secret and no user path
belongs in an artifact that is committed to the repository and read months later.

#### Scenario: A deploy writes the host block

- **WHEN** `get_git_information()` runs on a host whose name and processor model are both readable
- **THEN** the returned mapping carries a `host` entry
- **AND** that entry holds a non-empty `hostname` and a non-empty `cpu_model`

#### Scenario: An unreadable processor model does not fail the deploy

- **WHEN** the processor model cannot be read from `/proc/cpuinfo` and `platform.processor()` returns nothing
- **THEN** `get_git_information()` returns normally and raises nothing
- **AND** the `host` entry holds the hostname with `cpu_model` set to `None`

#### Scenario: An unreadable hostname records no host at all

- **WHEN** the hostname cannot be read
- **THEN** `get_git_information()` returns normally and raises nothing
- **AND** the `host` entry is `None` rather than a mapping whose `hostname` is `None`

### Requirement: Every artifact's metadata carries the host exactly once

The benchmark harness SHALL write a `host` key into every artifact's `metadata`, holding either the recorded host object or `null`, and SHALL leave no second copy of that block inside `metadata.git_info`.

`add_metadata` (`src/bin/service_benchmark.py:448`) assigns the whole loaded YAML file to
`metadata["git_info"]`. That assignment stores a reference to the loaded mapping, not a
copy, so the host block must be lifted out of the mapping **before** the metadata literal is
built. A lift performed afterwards would still read as correct while depending on evaluation
order.

The three states are distinct facts and SHALL stay distinct. The key is **absent** when the
artifact predates this field. The value is **`null`** when the deploy predates the field or
capture failed. The value is an **object** when the host is known. The harness SHALL never
write an empty string and SHALL never write a placeholder such as `"unknown"`.

The harness SHALL also write a `host_captured_at` string beside `host`, stating that the
host was captured at deploy time and that a container cannot move machines. Issue #433 asks
for that caveat "in the artifact and in the docs", and the field is the only part of this
change a reader holding just the JSON can see. The precedent is `git_info_captured_at`
(`src/bin/service_benchmark.py:464`), whose own comment says to state such a caveat in the
artifact rather than in a source comment.

#### Scenario: A deploy that recorded a host produces an artifact naming it

- **WHEN** `git_info.yaml` carries a `host` block and the harness writes an artifact
- **THEN** `metadata["host"]` equals that block
- **AND** `metadata["git_info"]` carries no `host` key
- **AND** `metadata["host_captured_at"]` states that the capture happened at deploy time

#### Scenario: A deploy that predates the field produces a null host

- **WHEN** `git_info.yaml` carries no `host` key and the harness writes an artifact
- **THEN** `metadata["host"]` is `null`
- **AND** the harness raises nothing

### Requirement: Both report formats render the three host readings distinctly

The markdown report and the HTML report SHALL each render three different texts for a host that is absent, a host that is `null`, and a host that is recorded.

`parse_benchmark_results` (`src/utils/generate_benchmark_report.py:78`) reads the host with a
module-level absent-key sentinel, matching the `_INGEST_NOT_RECORDED` pattern at `:58`. A
plain `.get()` would collapse "this artifact predates the field" and "capture failed" into
one text, which is the reading error the sentinel exists to prevent.

Both renderers currently return an empty block when neither a code version nor a config
version is present. That guard SHALL widen to admit a recorded host, so an artifact carrying
a host but no version digests still renders its host row. An artifact that records none of
the three still renders nothing.

A recorded host row SHALL name the hostname, and SHALL append the processor model only when
`cpu_model` is not `None`, so a partial capture never renders a machine as `h1 (None)`.

Each report SHALL state that a deploy-time host does not carry the freeze defect that
`git_info.last_commit` carries. A container cannot move to another machine, so a re-run
against an existing stack ran on the host that deployed it. The reports render that
statement from `metadata.host_captured_at`, beside the host, the way the deploy-commit row
already carries its own caveat (`src/utils/generate_benchmark_report.py:262` and `:1022`).

#### Scenario: A recorded host appears in both reports, with its caveat

- **WHEN** an artifact's `metadata.host` is an object naming a hostname and a processor model
- **THEN** the markdown provenance block names that hostname
- **AND** the HTML provenance block names that hostname
- **AND** each block states that the host was captured at deploy time and did not go stale

#### Scenario: An older artifact says the field is absent, not that capture failed

- **WHEN** an artifact's `metadata` carries no `host` key
- **THEN** each report renders its absent-key text
- **AND** that text differs from the text each report renders for a `null` host

#### Scenario: A host with no processor model renders no empty parenthesis

- **WHEN** an artifact's `metadata.host` names a hostname and carries `cpu_model` set to `None`
- **THEN** each report names the hostname
- **AND** neither report renders the literal text `None` as part of the host

### Requirement: The comparison tool names each arm's host and flags a mismatch

`compare_runs.py` SHALL print the recorded host of every arm in its provenance table, and SHALL print a warning when two arms recorded different hostnames.

The warning SHALL NOT change the exit code and SHALL NOT refuse a comparison on that ground
alone. A refusal rule changes the campaign plan and the pre-registration, and it needs an
operator decision. This requirement makes a cross-host comparison visible to the reader; it
does not decide what the reader must do about it.

An arm that recorded no host is unknowable, not mismatched, and SHALL NOT raise the warning.
This is the reading the corpus gate already gives an unrecorded fingerprint
(`scripts/benchmarking/compare_runs.py:486-487`).

Today a cross-host **retrieval** arm is caught only as a side effect, because its corpus
fingerprint differs. A cross-host **ingest** arm is caught by nothing:
`--corpus-differs-by-design` (`scripts/benchmarking/compare_runs.py:519`) relaxes the one
check that would have caught it, and the run then passes every gate and prints a verdict.

#### Scenario: Two arms from two hosts are flagged

- **WHEN** two arms record different hostnames and `compare_runs.py` compares them
- **THEN** the provenance table names both hostnames
- **AND** the report prints a warning that the arms ran on different hosts
- **AND** the exit code is the one the gates alone would have produced

#### Scenario: An arm that recorded no host raises no warning

- **WHEN** one arm records a host and the other carries no `host` key
- **THEN** the provenance table shows the recorded host and reports the other as not recorded
- **AND** the report prints no host-mismatch warning
