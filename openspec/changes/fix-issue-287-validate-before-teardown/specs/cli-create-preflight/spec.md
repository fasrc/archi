## ADDED Requirements

### Requirement: No destructive step precedes a step that can refuse the deployment

`archi create` SHALL complete every step that is capable of refusing the deployment —
service selection, configuration validation, required-secret validation, and construction
of the compose plan — before it performs any destructive action against the existing
deployment. A `--force` teardown MUST NOT run until the replacement deployment is known to
be both valid and constructible.

The test is not "does this step read the deployment directory" but "can this step fail". A
step that cannot touch the old deployment can still refuse the new one, and refusing after
the teardown is exactly the defect. Compose-plan construction is included for this reason:
`ServiceBuilder.build_compose_config()` calls `_discover_repo_path()` under `--dev`, which
raises when no ancestor directory contains `pyproject.toml`
(`src/cli/utils/service_builder.py:10-18, 198-200`).

The operator-visible contract is that a `create` which was always going to fail leaves the
existing deployment exactly as it found it.

#### Scenario: Forced re-create with grafana enabled and no env file

- **WHEN** `archi create --force --services chatbot,grafana` is invoked against an existing
  deployment with no `--env-file`, so `SecretsManager` falls back to
  `src/cli/managers/secrets_dummy.env` and the required `GRAFANA_PG_PASSWORD` is absent
- **THEN** the command exits non-zero
- **AND** `delete_deployment()` is never called
- **AND** the existing deployment directory and its contents are left intact
- **AND** the error names the missing secret and mentions `--env-file`

#### Scenario: Forced re-create with any other required secret missing

- **WHEN** `archi create --force` is invoked against an existing deployment with an env file
  that omits a secret required by an enabled service other than grafana
- **THEN** the command exits non-zero
- **AND** `delete_deployment()` is never called
- **AND** the existing deployment directory and its contents are left intact

This scenario exists because the defect is an ordering defect, not a grafana defect. A fix
that special-cases grafana would satisfy the scenario above and fail this one.

#### Scenario: Forced re-create whose compose plan cannot be constructed

- **WHEN** `archi create --force --dev` is invoked against an existing deployment in a
  location where `_discover_repo_path()` raises because no ancestor contains
  `pyproject.toml`
- **THEN** the command exits non-zero
- **AND** `delete_deployment()` is never called
- **AND** the existing deployment directory and its contents are left intact

This scenario distinguishes "teardown moved below secret validation" from "teardown moved
below everything that can refuse". Only the latter passes it.

#### Scenario: Forced re-create that passes every check

- **WHEN** `archi create --force` is invoked against an existing deployment with a valid
  config, every required secret present, and a constructible compose plan
- **THEN** the existing deployment is torn down and replaced, as it is today
- **AND** the teardown happens before the new deployment directory is created

### Requirement: Refusing an existing deployment stays fail-fast

`archi create` without `--force` SHALL refuse an existing deployment before validating
configuration and secrets, so the operator is told the deployment already exists rather than
being told about an unrelated config problem in a deployment they were not going to replace.
This preserves the error precedence that exists today.

#### Scenario: Existing deployment, no --force, valid config

- **WHEN** `archi create` is invoked without `--force` against an existing deployment
- **THEN** the command exits non-zero with a message stating the deployment already exists
  and naming both `--force` and `archi delete` as remedies

#### Scenario: Existing deployment, no --force, and also an invalid config

- **WHEN** `archi create` is invoked without `--force` against an existing deployment, and
  the supplied configuration would additionally have failed validation
- **THEN** the command reports that the deployment already exists
- **AND** does not report the configuration error instead, because the operator's first
  problem is that they did not ask to replace anything

### Requirement: Dry runs report the teardown they would perform, and only when they would

`archi create --dry --force` SHALL report that it would remove the existing deployment when
the run reaches the point at which a real run would perform the teardown, and SHALL NOT
remove it. When the dry run fails an earlier check, the notice MUST NOT be printed, because
a real run with the same inputs would have failed before reaching the teardown and would
therefore not have removed anything. Printing it in that case would misreport the effect of
the real run — the opposite of what a dry run is for.

#### Scenario: Dry forced re-create with otherwise valid inputs

- **WHEN** `archi create --dry --force` is invoked against an existing deployment with a
  valid config, every required secret present, and a constructible compose plan
- **THEN** the output states that it would remove the existing deployment at that path
- **AND** `delete_deployment()` is never called
- **AND** the existing deployment directory is left intact
- **AND** the dry-run summary still prints and the command exits 0

#### Scenario: Dry forced re-create that fails validation

- **WHEN** `archi create --dry --force` is invoked against an existing deployment with a
  required secret missing
- **THEN** the command exits non-zero
- **AND** the existing deployment directory is left intact
- **AND** the "would remove existing deployment" notice is NOT printed, because a real run
  with these inputs would have refused before reaching the teardown

### Requirement: Splitting the teardown helper preserves every existing caller

Refactoring `handle_existing_deployment()` SHALL NOT change the observable behaviour of any
command other than `archi create`. In particular `archi evaluate --force` depends on the
destructive branch running at its current call site: it invokes the helper at
`src/cli/cli_main.py:748-750` and then raises "Benchmarking runtime already exists" at
`:752-755` if the directory is still present. A split that leaves only the non-destructive
half behind that call site would make every forced evaluate against an existing benchmark
runtime fail.

#### Scenario: Forced evaluate against an existing benchmarking runtime

- **WHEN** `archi evaluate --force` is invoked against an existing benchmarking runtime
  directory
- **THEN** the existing runtime directory is removed and the command proceeds past the
  "Benchmarking runtime already exists" check, exactly as it does today

#### Scenario: Evaluate without --force against an existing benchmarking runtime

- **WHEN** `archi evaluate` is invoked without `--force` against an existing benchmarking
  runtime directory
- **THEN** the command exits non-zero, exactly as it does today
