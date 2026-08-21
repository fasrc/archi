## ADDED Requirements

### Requirement: A port the operator configured is validated, never discarded

`archi create` SHALL validate every port value the configuration supplies — including `0`, `""`, and an explicit `null` — before it performs the `--force` teardown.

A check that discards a value before validating it is not a check. `_normalize_port()` already
refuses all three of these values with a message naming the service and the config path
(`src/cli/managers/templates_manager.py:168-179`); the truthiness guards in
`extract_port_config()` (`:235,237`) and the `is None` skip in `validate_port_config()`
(`:261`) are the only reason it never sees them. Measured on `origin/dev` at `2c404822`, a
host-mode `services.chat_app.port` of `0`, `""`, or `null` yields `errors == []` from the
pre-teardown check, so `create --force` removes the running deployment (`cli_main.py:277`)
and fails afterwards.

The value the check refuses must be the value the operator wrote. Silently substituting a
registry default for a configured-but-invalid port is the same defect wearing different
clothes: a config that was always going to be refused instead deploys, on a port nobody
asked for.

#### Scenario: Forced re-create with a service configured `port: 0`

- **WHEN** `archi create --force` is invoked against an existing deployment with an enabled
  service whose config sets `port: 0`
- **THEN** the command exits non-zero
- **AND** `delete_deployment()` is never called
- **AND** the existing deployment directory and its contents are left intact
- **AND** the error names the service and the configuration path, as `_normalize_port` does

#### Scenario: Forced re-create with a service configured `port: ""`

- **WHEN** the same invocation is made with `port: ""` instead of `port: 0`
- **THEN** the command exits non-zero
- **AND** `delete_deployment()` is never called
- **AND** the existing deployment directory and its contents are left intact

This scenario exists because the defect is not about the number zero. It is about a
truthiness test standing in for a validity test, and every falsy value is in scope.

#### Scenario: Forced re-create with a service configured `port: null`

- **WHEN** the same invocation is made with an explicit `port: null` in the YAML
- **THEN** the command exits non-zero
- **AND** the existing deployment directory and its contents are left intact
- **AND** this outcome differs from omitting the key, which is not an error

An explicit `null` is something the operator wrote, so it is a configuration error. This
scenario pins the decision, because `.get(key, default)` cannot tell an explicit `null` from
an absent key and an implementation built on it will silently pick the other reading.

#### Scenario: Non-host-mode forced re-create with `port: 0`

- **WHEN** `archi create --force` is invoked **without** `--hostmode` against an existing
  deployment, with an enabled service whose config sets `port: 0`
- **THEN** the command exits non-zero
- **AND** the existing deployment directory and its contents are left intact

In non-host mode `port` is the container-side value and the host side comes from
`external_port` or the registry default, so a fix that inspects only the host side satisfies
the host-mode scenarios and fails this one.

#### Scenario: The availability probe is offered no value it is not offered today

- **WHEN** any configuration that this requirement newly refuses is validated
- **THEN** `_probe_port()` is never called for the refused value, because normalization
  raises inside `validate_port_config()` before the probe loop is reached
- **AND** the set of values reaching the probe is otherwise unchanged, since only values
  that normalize into `1..65535` are ever probed

#### Scenario: A container port shared by two enabled services is still accepted

- **WHEN** `archi create` is invoked with two enabled services whose container ports are
  equal but whose host ports differ, as `chatbot` and `grader` are configured by default
- **THEN** no "assigned to multiple services" error is reported
- **AND** the command is not refused on that basis

Container ports occupy separate network namespaces, so sharing one is legal. This scenario
fences the previous one: validating a configured container value must check its validity
without entering it into duplicate detection, or the default registry refuses itself.

### Requirement: An unconfigured port keeps falling back, and a portless service stays portless

`archi create` SHALL continue to fall back to the registry defaults when the configuration does not supply a port, and SHALL NOT report an error for a service that has no port at all.

The distinction being drawn is "configured as invalid" versus "not configured". Only the
first is an error. Encoding it wrongly breaks far more than it fixes: `postgres`, `piazza`,
`mattermost`, `redmine-mailer`, and `benchmarking` carry no port default and no port config
path (`src/cli/service_registry.py:34`), and `postgres` is auto-enabled, so treating their
absent value as a configured `null` refuses every host-mode create.

#### Scenario: The service section is absent from the configuration

- **WHEN** `archi create` is invoked with a configuration that omits an enabled service's
  port section entirely
- **THEN** the service's ports come from the registry defaults
- **AND** no error is reported

#### Scenario: The service section is present but sets no port

- **WHEN** the section exists and carries other keys but no port key
- **THEN** the service's ports come from the registry defaults
- **AND** no error is reported, because an absent key is not a configured value

#### Scenario: An enabled service that has no port and no port configuration path

- **WHEN** `archi create --force --hostmode` is invoked with `postgres` enabled, as it is
  automatically
- **THEN** no port error is reported for `postgres`
- **AND** the command's verdict is exactly what it is today

This scenario is the regression guard for the whole requirement. An implementation that
emits an absent port as `null` and then validates it produces
`Invalid port value 'None' for postgres` on every host-mode create.
