## MODIFIED Requirements

### Requirement: No destructive step precedes a step that can refuse the deployment

`archi create` SHALL complete every step that is capable of refusing the deployment —
service selection, configuration validation, required-secret validation, construction
of the compose plan, and the configuration-only port checks (port-value normalization
and duplicate-assignment detection) — before it performs any destructive action against
the existing deployment. A `--force` teardown MUST NOT run until the replacement
deployment is known to be both valid and constructible.

The test is not "does this step read the deployment directory" but "can this step fail". A
step that cannot touch the old deployment can still refuse the new one, and refusing after
the teardown is exactly the defect. Compose-plan construction is included for this reason:
`ServiceBuilder.build_compose_config()` calls `_discover_repo_path()` under `--dev`, which
raises when no ancestor directory contains `pyproject.toml`
(`src/cli/utils/service_builder.py:10-18, 198-200`).

The port-availability probe is deliberately NOT in the enumerated list. It is not a
configuration check: it asks the host whether a port is free, and before the teardown the
existing deployment is still running and still holding its ports, so probing early would
refuse every re-create that reuses the old deployment's ports. The probe runs after the
teardown; the requirement "Port validation separates configuration checks from the
availability probe" pins that split.

The operator-visible contract is that a `create` which was always going to fail leaves the
existing deployment exactly as it found it.

**Scope, stated honestly.** This requirement is satisfied for the checks enumerated above.
The general problem — that every stage of `prepare_deployment_files()` runs after the
teardown and any of them can raise on deterministic config input — is tracked as
`fasrc/archi#294`, which proposes rendering the replacement before destroying the existing
deployment rather than enumerating routes one at a time. Three review rounds on
`fasrc/archi#287` found four separate routes, which is the evidence that enumeration does
not converge; the port route closed here (`fasrc/archi#293`) was the fifth.

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

#### Scenario: Forced re-create with an invalid port value

- **WHEN** `archi create --force` is invoked against an existing deployment with a config
  that assigns an enabled service a truthy host-side port value — the `port` key in host
  mode, `external_port` otherwise — that is nonnumeric or out of range
- **THEN** the command exits non-zero with an error naming the value, the service, and the
  config location
- **AND** `delete_deployment()` is never called
- **AND** the existing deployment directory and its contents are left intact

The host-side scoping is deliberate and matches what the checks validate today, moved —
not extended — by this change: container-side `port` values in non-host mode are never
normalized, and falsy values (`0`, an empty string) are dropped by the derivation before
any check sees them. Widening validation to those cases would be a behaviour change
beyond reordering, and is not promised here.

#### Scenario: Forced re-create with one port assigned to two enabled services

- **WHEN** `archi create --force` is invoked against an existing deployment with a config
  that assigns the same host port to more than one enabled service
- **THEN** the command exits non-zero with an error naming the port and the services that
  claim it
- **AND** `delete_deployment()` is never called
- **AND** the existing deployment directory and its contents are left intact

#### Scenario: Dry forced re-create with an invalid or duplicated port

- **WHEN** `archi create --dry --force` is invoked with a config carrying an invalid or
  duplicated host port
- **THEN** the command exits non-zero instead of reporting success
- **AND** the "would remove existing deployment" notice is NOT printed, because a real run
  with these inputs would have refused before reaching the teardown

#### Scenario: Plain dry run with an invalid or duplicated port

- **WHEN** `archi create --dry` is invoked without `--force` with a config carrying an
  invalid or duplicated host port
- **THEN** the command exits non-zero instead of reporting success, because the port
  checks are not gated on `--force` — a dry run mirrors what the corresponding real run
  would do

Before this change, every dry run returned before the port checks ran and reported
success on a config a real run would refuse — the dry run misreported the real run, which
is the opposite of what a dry run is for.

#### Scenario: Forced re-create that passes every check

- **WHEN** `archi create --force` is invoked against an existing deployment with a valid
  config, every required secret present, and a constructible compose plan
- **THEN** the existing deployment is torn down and replaced, as it is today
- **AND** the teardown happens before the new deployment directory is created

## ADDED Requirements

### Requirement: Port validation separates configuration checks from the availability probe

`archi create` SHALL derive the ports it validates in exactly one implementation and SHALL
split port validation into configuration-only checks (value normalization and
duplicate-assignment detection) that run before the `--force` teardown, and a
port-availability probe that runs only after it. The pre-teardown checks MUST NOT probe
whether a port is free on the host, and the probe MUST NOT be reimplemented or relocated
above the teardown.

The split is the whole point, in both directions. The configuration checks depend only on
the config and the service registry, so running them after a teardown converts a knowable
config error into a destroyed deployment. The probe depends on the host's live port state,
and before the teardown the existing deployment still holds its ports — an early probe
would report a false conflict for every port the replacement reuses, refusing exactly the
re-creates that should succeed.

One derivation, shared: the port values reach both **validation** call sites — the
pre-teardown check in `create()` and the pre-probe check inside template preparation —
through the same `port_config_path` walk and the same host/container default resolution.
Two independent derivations that must agree is the defect class found twice on the
`fasrc/archi#287` review, and a spec-level constraint here so it is not reintroduced
route by route. Scoped honestly: one pre-existing walk lives outside the validation path
— the success-banner URL printer `show_service_urls()` (`src/cli/utils/helpers.py`),
whose fallback semantics diverge from the derivation used here. It cannot destroy a
deployment and consolidating it changes printed output, so it is excluded from this
requirement and tracked by a follow-up issue rather than silently grandfathered.

#### Scenario: Re-create reusing the existing deployment's ports

- **WHEN** `archi create --force` replaces a deployment whose replacement assigns the same
  host ports the running deployment currently holds, with a valid config
- **THEN** the pre-teardown checks do not refuse the deployment on account of those ports
  being in use
- **AND** the availability probe runs only after the existing deployment is torn down and
  has released them

#### Scenario: Port reuse opt-out skips only the probe

- **WHEN** a caller passes `allow_port_reuse` (the option that permits ports already in use
  on the host)
- **THEN** the availability probe is skipped
- **AND** duplicate-assignment detection still runs and still refuses a config that assigns
  one port to several enabled services, exactly as it does today

#### Scenario: The port derivation exists once on the validation path

- **WHEN** the pre-teardown checks in `create()` and the pre-probe checks inside template
  preparation compute the port map for the same plan and config
- **THEN** both call the same derivation code, and a search of `src/` finds exactly one
  implementation of the `port_config_path` config walk on the validation path — the only
  other walk is the display-path `show_service_urls()`, excluded above and tracked by a
  follow-up issue
