## ADDED Requirements

### Requirement: No destructive step in `archi evaluate` precedes a step that can refuse the benchmarking run

`archi evaluate` SHALL complete every step that is capable of refusing the benchmarking run — question-bank preflight, source resolution, configuration validation, required-secret validation, and construction of the compose plan — before it performs any destructive action against the existing benchmarking runtime. A `--force` teardown MUST NOT run until the replacement runtime is known to be both valid and constructible.

This is the `archi evaluate` half of the invariant this capability already states for `archi
create`. It was left open deliberately: fasrc/archi#287 preserved `evaluate()`'s behaviour
byte-for-byte so that a change about `create` did not also alter the benchmarking path, and
filed fasrc/archi#290 to close it separately.

The same test applies — *can this step fail*, not *does this step read the runtime
directory*. Compose-plan construction is included for that reason, and is the lower bound on
where the teardown may sit.

The operator-visible contract is that an `evaluate` which was always going to fail leaves the
existing benchmarking runtime exactly as it found it, including the ingested corpus it holds.
That corpus is the reason this matters more here than the wording suggests: re-ingesting it
costs roughly fifty minutes, so a runtime destroyed by a refusable failure is not a cheap
loss.

**Scope, stated honestly.** This requirement is satisfied for the steps enumerated above. It
is not universally true: every stage of `TemplateManager.prepare_deployment_files()` still
runs after the teardown and any of them can raise. The port-availability probe inside that
call is a deliberate exception rather than an oversight — the existing runtime holds its
ports until it is removed, so the probe cannot be hoisted above the removal without becoming
a guaranteed false failure. `fasrc/archi#294` tracks the structural answer for both commands:
render the replacement before destroying the existing deployment, instead of enumerating
refusable steps one at a time.

#### Scenario: Forced evaluate with a required secret missing

- **WHEN** `archi evaluate --force` is invoked against an existing benchmarking runtime with
  a valid benchmarking configuration and an env file that omits a secret required by an
  enabled service
- **THEN** the command exits non-zero
- **AND** `delete_deployment()` is never called
- **AND** the existing runtime directory and its contents are left intact

#### Scenario: Forced evaluate whose configuration cannot be validated

- **WHEN** `archi evaluate --force` is invoked against an existing benchmarking runtime with
  a configuration that declares no `services.benchmarking` block, so `validate_configs`
  refuses it
- **THEN** the command exits non-zero
- **AND** `delete_deployment()` is never called
- **AND** the existing runtime directory and its contents are left intact

This scenario exists because the defect is an ordering defect, not a secrets defect. A fix
that moved the teardown below secret validation alone would satisfy the scenario above and
fail this one, since configuration validation runs earlier still.

#### Scenario: Forced evaluate that passes every check

- **WHEN** `archi evaluate --force` is invoked against an existing benchmarking runtime with
  a valid benchmarking configuration, every required secret present, and a constructible
  compose plan
- **THEN** the existing runtime is torn down, as it is today
- **AND** the teardown happens after the compose plan is constructed and before the
  replacement runtime directory is created

#### Scenario: Evaluate without --force against an existing benchmarking runtime

- **WHEN** `archi evaluate` is invoked without `--force` against an existing benchmarking
  runtime directory
- **THEN** the command exits non-zero reporting that the runtime already exists, before it
  validates configuration or secrets, preserving the error precedence it has today
- **AND** `delete_deployment()` is never called

## MODIFIED Requirements

### Requirement: Splitting the teardown helper preserves every existing caller

Refactoring `handle_existing_deployment()` SHALL NOT silently drop the destructive branch at any call site that depends on it. `archi evaluate --force` is such a caller: it invokes the teardown and then raises "Benchmarking runtime '{name}' already exists" if the directory is still present, so a split leaving only the non-destructive half behind that call site would make every forced evaluate against an existing runtime fail.

The `base_dir.exists()` refusal in `evaluate()` is a **post-teardown assertion**, not a
precondition, and MUST stay immediately below the teardown wherever the teardown sits.
`remove_existing_deployment()` downgrades a failed cleanup to a warning, and this check is
the only thing that turns that warning into a refusal rather than letting the run write a
replacement into a directory it failed to clear. Without `--force` the check is unreachable,
because `handle_existing_deployment()` has already refused.

The two calls are therefore **not required to be adjacent**. `handle_existing_deployment()`
stays early, so the no-`--force` refusal keeps its precedence; the teardown moves down to
satisfy "no destructive step precedes a step that can refuse the benchmarking run" above.
What must be preserved is that the destructive branch still runs on the forced path, and that
the assertion still follows it. (Cited by symbol, not line number, because both move.)

#### Scenario: Forced evaluate against an existing benchmarking runtime

- **WHEN** `archi evaluate --force` is invoked against an existing benchmarking runtime
  directory and every refusable check passes
- **THEN** the existing runtime directory is removed and the command proceeds past the
  "Benchmarking runtime already exists" check

#### Scenario: A forced teardown that silently fails is still caught

- **WHEN** `archi evaluate --force` reaches the teardown and the removal fails, which
  `remove_existing_deployment()` reports as a warning rather than raising
- **THEN** the command exits non-zero reporting that the benchmarking runtime already exists,
  rather than proceeding to write a replacement into the directory it failed to clear

#### Scenario: Evaluate without --force against an existing benchmarking runtime

- **WHEN** `archi evaluate` is invoked without `--force` against an existing benchmarking
  runtime directory
- **THEN** the command exits non-zero, exactly as it does today
