## ADDED Requirements

### Requirement: Validation precedes destructive teardown

`archi create` SHALL complete every validation that can refuse the deployment — service
selection, configuration, and required secrets — before it performs any destructive step
against the existing deployment. A `--force` teardown MUST NOT run until the replacement
deployment is known to be satisfiable.

The operator-visible contract is that a `create` which was always going to fail leaves the
existing deployment exactly as it found it. A failure that destroys the running deployment
and then declines to replace it is strictly worse than the same failure with nothing
destroyed, because the operator loses a working system to learn something the command could
have known first.

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

#### Scenario: Forced re-create that passes validation

- **WHEN** `archi create --force` is invoked against an existing deployment with a valid
  config and every required secret present
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

### Requirement: Dry runs report the teardown they would perform

`archi create --dry --force` SHALL continue to report that it would remove the existing
deployment, and SHALL NOT remove it. Relocating the teardown MUST NOT silently drop this
notice from the dry-run output, since the dry run's purpose is to tell the operator what a
real run would do — and destroying the existing deployment is the most consequential part
of what it would do.

#### Scenario: Dry forced re-create against an existing deployment

- **WHEN** `archi create --dry --force` is invoked against an existing deployment
- **THEN** the output states that it would remove the existing deployment at that path
- **AND** `delete_deployment()` is never called
- **AND** the existing deployment directory is left intact
- **AND** the dry-run summary still prints and the command exits 0
