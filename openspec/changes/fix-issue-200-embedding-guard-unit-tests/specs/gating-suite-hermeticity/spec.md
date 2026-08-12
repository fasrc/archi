## ADDED Requirements

### Requirement: The network-guard classifier is exercised by the gating suite

The logic that decides whether a failed weights fetch means "the network could not answer" SHALL be exercised by the suite that gates every pull request.

A guard whose classification is checked by no automatic run can rot in either direction, and both
directions are silent. Widened, it converts a definitive answer — a renamed, removed or gated model
repository, or a malformed endpoint of our own — into a skip, and the deliberate benchmark reports
green while exercising nothing. Narrowed, it lets a genuine outage fail a deliberate run and teaches
the reader to disbelieve the suite. Neither shows up in the gate's result, so neither is noticed at
the time it is introduced; the over-broad-base defect specifically was introduced three review rounds
in a row, once per HTTP library.

The gated tests SHALL drive the classifier directly with synthetic exceptions, and MUST NOT import
the embedding library or reach the network to do it. The classification question — "does this
exception mean no usable answer came back" — is answerable from the exception object alone, so
neither is needed; requiring either would put a third-party import and a network dependency back on
the critical path of every pull request, which is precisely what keeping these benchmarks out of the
gate exists to prevent.

The invariant that every named transport type is an allowlist member — meaning "no usable answer came
back" for itself *and* for all of its subclasses — SHALL be one of the gated checks, by inspecting
the subclasses of each named type. This is the invariant that the recurring defect violates, so
leaving it outside the gate would leave the change's purpose unmet.

A negative gated test MUST fail loudly rather than report skipped when the classifier absorbs what it
was given. A test that asserts "this is not converted into a skip" and is itself written so that a
swallowed error propagates a skip out of the assertion reports green in the one case it exists to
catch.

The gated checks SHALL be verified by mutation: with the classifier deliberately broken, the gate is
observed to go red before the change is considered complete. Observing that the new tests pass proves
they run, not that they bind.

#### Scenario: Widening the allowlist reds a pull request

- **WHEN** a named network exception type is replaced by, or joined with, a base class that also
  covers a client-side or definitive error
- **THEN** the gating suite fails and names the offending type
- **AND** the failure occurs without the embedding library being imported

#### Scenario: A broken classifier reds a pull request

- **WHEN** the classifier is mutated to report every exception as a network failure
- **OR** an entire exception family is dropped from the allowlist
- **THEN** the gating suite fails

#### Scenario: A local defect is still not a network outage

- **WHEN** the classifier is given an assertion failure, or an `OSError` carrying an errno that is
  not in the network table
- **THEN** the gated test reports that it is not classified as a network failure
- **AND** that test fails loudly rather than reporting skipped if the classification is wrong

#### Scenario: An error status is classified by what the host actually said

- **WHEN** the classifier is given a failure carrying a definitive client error status
- **THEN** it reports a non-outage, so a deliberate run would fail rather than skip
- **AND WHEN** it is given a rate-limit status, any server-side status including a fronting CDN's
  vendor-specific codes, or no status at all
- **THEN** it reports an outage

#### Scenario: A missing module skips but a broken environment does not

- **WHEN** the import helper is asked for an attribute of a genuinely absent module
- **THEN** it reports a skip naming the missing module
- **AND WHEN** the module is installed but raises an import error from inside itself
- **THEN the error propagates** rather than being reported as "not installed"

#### Scenario: The gated tests are hermetic

- **WHEN** the gate collects and runs its suite with no network access
- **THEN** the classifier tests run and pass
- **AND** no embedding library is imported by them

### Requirement: The guard's classification logic has a single definition

The classification logic SHALL exist as exactly one definition in the repository, imported by both the gating suite and the deliberately-run embedding suite.

Two copies — one gated, one run by hand — would let the gate certify a classifier that is not the
one the benchmarks use, which is worse than not gating it at all: the green result would be about the
wrong object, and nothing would report the divergence.

The definition SHALL live outside both suites, in a location importable by each, and its import MUST
resolve under the project's documented commands for running them, including the subprocess run that
re-executes the deliberate suite with the embedding library made unimportable.

Relocating the logic MUST NOT require editing the gate script, the test-path configuration, or any CI
workflow. The gating suite is defined by an explicit path, so gating a new test means placing it
inside that path.

#### Scenario: Both suites agree about the allowlist

- **WHEN** the allowlist is changed
- **THEN** the gated check and the deliberate suite's check both see the change
- **AND** neither can pass while the other fails on the same allowlist contents

#### Scenario: The deliberate suite still degrades to a skip without the embedding library

- **WHEN** the embedding library is made unimportable and the documented deliberate command is run
- **THEN** the extracted module still imports
- **AND** the suite reports skips naming the missing library rather than failures or collection errors

#### Scenario: Gating the logic moves no control-plane file

- **WHEN** the classifier's gated tests are added
- **THEN** the test-path configuration, the gate script and the CI workflows are unchanged
