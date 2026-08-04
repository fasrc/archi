## ADDED Requirements

### Requirement: The pull-request gating suite does not depend on an external network

The test suite that gates every pull request SHALL be hermetic with respect to the public internet.
No test collected by the gate may download model weights, datasets, or any other artifact from a
third-party host at test time. The availability of an external CDN must not be able to determine
whether a pull request is red or green.

A test that legitimately requires a real external resource belongs outside the gating suite, where
it is run deliberately. Relocating such a test MUST NOT require editing the gate script, the CI
workflow, or any other control-plane file: the gating suite is defined by an explicit path, so a
test placed outside that path is outside the gate by construction.

#### Scenario: A CDN outage cannot red an unrelated pull request

- **WHEN** the HuggingFace CDN is unreachable
- **AND** a pull request that touches no ingestion or embedding code is gated
- **THEN** no test in the gating suite attempts to download model weights
- **AND** the gating job's result is unaffected by the outage

#### Scenario: The embedding benchmarks are not collected by the gate

- **WHEN** the gate runs its test suite
- **THEN** neither the embedding-model test nor the embedding-performance benchmark is collected
- **AND** the gating job does not download the `all-MiniLM-L6-v2` weights

### Requirement: A network failure during a deliberate embedding run is a loud skip

An embedding test that cannot reach the model weights SHALL be reported as skipped with a reason
that names the network, and SHALL NOT fail and SHALL NOT pass silently. The three outcomes are
distinct and only one is permitted for an unreachable-weights condition: a failure misattributes an
infrastructure outage to the code under test, and a silent pass lets a permanently broken embedding
path hide behind a green suite.

The skip reason MUST contain wording identifying the network or the unreachable weights, so a
reader of a green run can tell that the embedding path went untested and why. A generic reason such
as "unavailable" does not satisfy this requirement.

The existing missing-library outcome is preserved: when the embedding library itself is absent, the
test still skips, and that skip is reported distinguishably from the network case. This outcome
SHALL hold for **every** test in the file, including the guard tests that police the skip logic
itself. A guard test that reports a failure where the code it guards would report a skip breaks the
contract it exists to defend — and it breaks it in the one environment, a minimal install, where the
reader has least reason to suspect the test rather than the code.

#### Scenario: Unreachable weights skip with a network reason

- **WHEN** an embedding test is run deliberately
- **AND** constructing the embedding model raises a transport or offline error because the weights
  cannot be fetched
- **THEN** the test is reported as skipped
- **AND** the skip reason names the network or the unreachable weights
- **AND** the test does not fail

#### Scenario: A missing library still skips on its own terms

- **WHEN** the embedding library is not installed
- **THEN** the test is reported as skipped for the missing library
- **AND** that reason is distinguishable from the network reason

#### Scenario: The guard tests skip too, rather than failing, without the library

- **WHEN** the embedding library is not installed
- **AND** the documented benchmark command is run
- **THEN** the guard tests are reported as skipped for the missing library, like the benchmarks
- **AND** no test in the file is reported as failed or errored

### Requirement: The network guard names specific exception types

The guard that converts an unreachable-weights condition into a skip SHALL catch a named, bounded
set of exception types and SHALL NOT catch bare `Exception`. A bare catch would absorb an
`AssertionError` from a genuine embedding regression and turn the test into one that can never
fail, which defeats the reason for keeping it.

The named set MUST cover both the transport errors raised by a live outage and the offline or
local-entry errors raised when the library is forced into offline mode with a cold cache. These are
different exception families, and a guard covering only the family used to simulate the failure
would still red a pull request during a genuine outage.

Assertion failures MUST continue to propagate as failures.

#### Scenario: A genuine embedding regression still fails

- **WHEN** the embedding model is reachable and returns embeddings of the wrong dimension
- **THEN** the test fails on its assertion
- **AND** the failure is not converted into a skip

#### Scenario: Both failure families are covered

- **WHEN** the guard is exercised with a transport error of the kind a live CDN outage raises
- **AND** separately with the offline or local-entry error raised under forced offline mode
- **THEN** each is caught and converted into a skip naming the network
- **AND** neither escapes as a test failure

### Requirement: The embedding benchmarks still assert when the network is available

The relocated tests SHALL execute their assertions whenever the model is reachable, and SHALL NOT
become permanent skips. A guard that skips unconditionally, or that skips because of a defect in
the guard itself, is indistinguishable from deleting the tests.

The embedding assertions are retained as they stand, including that two input texts produce two
embeddings and that an embedding has 384 dimensions.

A reply from the model host is NOT an outage. When the host answers with a definitive error status —
the model repository renamed, removed, or gated behind credentials — the benchmark SHALL fail rather
than skip, because that is a broken model dependency and skipping it would make the benchmarks
permanently green while nothing is being exercised. Only statuses that mean "reached, but cannot
serve it right now" — request timeouts, rate limiting, server-side errors — may be treated as an
outage, alongside failures where no status came back at all. A failure part-way through a transfer
carries a success status and is still an outage, so the status is decisive only when it is itself an
error.

#### Scenario: A removed or gated model repository fails the benchmark

- **WHEN** the model host is reached and answers with a definitive error status for the model
  repository, such as not-found or forbidden
- **THEN** the benchmark fails and names that error
- **AND** it is NOT reported as an unreachable network

#### Scenario: A transient host error is still an outage

- **WHEN** the model host answers with a rate-limit or server-side error status
- **OR** the failure carries no status at all, or a success status from an interrupted transfer
- **THEN** the benchmark is reported as skipped with a reason naming the network

#### Scenario: A reachable model runs the assertions

- **WHEN** the embedding model is reachable
- **AND** the embedding tests are run deliberately
- **THEN** both tests execute rather than skip
- **AND** the assertion on embedding count and the assertion on the 384-dimension vector are both
  evaluated

### Requirement: Benchmarks moved out of the gate remain discoverable

A test relocated out of the gating suite SHALL be documented with the command that runs it, so that
removing it from automatic execution does not make it unfindable. A benchmark that no job runs and
no document mentions is deleted in practice, which this change explicitly does not do.

The documentation MUST live alongside the project's existing instructions for deliberately-run
tests, and MUST state that the benchmark reaches the network and is therefore not part of the gate.

#### Scenario: A developer can find and run the benchmarks

- **WHEN** a developer consults the developer guide for how to run the embedding benchmarks
- **THEN** the guide gives the command that runs them
- **AND** states that they reach the network and are excluded from the gating suite
