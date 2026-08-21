## ADDED Requirements

### Requirement: The validated port is the port the deployment binds

`archi create` SHALL validate the same port value the rendered deployment will bind, for every mode and every configuration shape.

Port validation that refuses the wrong port is worse than no validation, because it reports
confidence about a value the deployment ignores. In host mode the effective port is
`external_port` whenever that key is present — `_apply_host_mode_port_overrides()`
(`src/cli/managers/templates_manager.py`) rewrites each service's `port` to `external_port`
before the compose file is rendered. Any derivation used for validation MUST therefore
resolve host mode to `external_port` when present, falling back to `port`, and MUST test
presence the same way the override does — `is not None`, not truthiness — so the two agree
on every input rather than only on the common ones.

This closes the gap left by the "Port validation separates configuration checks from the
availability probe" requirement, which guarantees the two *validation* call sites share one
derivation but not that the shared derivation agrees with the *rendered config*. Both
statements are needed: one derivation that is uniformly wrong satisfies the first
requirement and fails this one.

The consequence of failing this requirement is not cosmetic. The pre-teardown check runs
against a config the operator can still fix, so a missed duplicate on the real bind target
lets `create --force` destroy a working deployment for a replacement that cannot start,
and a reported conflict on an ignored value refuses a deployment that would have worked.
The availability probe consumes the same derivation, so it probes the wrong port too.

Scoped honestly, and both exclusions are tracked: the success-banner URL printer
`show_service_urls()` (`src/cli/utils/helpers.py`) walks the port config on the *display*
path with divergent fallbacks and is excluded here, as it already is by the requirement
above (`fasrc/archi#300`). Falsy configured port values are still dropped before validation
rather than being reported (`fasrc/archi#311`); this requirement governs which key the
derivation reads, not which values survive the truthiness guard downstream of it.

#### Scenario: Host mode with both keys set validates the bound port

- **WHEN** the port configuration for an enabled service is `{"port": 7861, "external_port": 9000}` and the deployment is in host mode
- **THEN** the derived host port is `9000`, the value the rendered deployment binds
- **AND** the derived container port is `9000` as well, because host mode has one port —
  `network_mode: host` makes the compose `ports:` mapping inert and the override rewrites
  `port` itself

#### Scenario: Host mode duplicate detected on the values actually bound

- **WHEN** two enabled services in host mode both set `external_port: 9000` while their
  `port` values differ from each other
- **THEN** validation reports that one port is assigned to multiple services
- **AND** it reports it before the `--force` teardown, so the existing deployment survives a
  configuration that could never have bound both services

#### Scenario: Host mode with no external_port is unchanged

- **WHEN** the port configuration for an enabled service in host mode sets `port` and omits
  `external_port` entirely
- **THEN** the derived host and container ports are that `port` value, exactly as before

#### Scenario: Non-host mode derivation is unchanged

- **WHEN** the port configuration for an enabled service is `{"port": 8000, "external_port": 9000}` and the deployment is not in host mode
- **THEN** the derived host port is `9000` and the derived container port is `8000`, exactly
  as before, because outside host mode the two ports are genuinely different and the
  published mapping is what binds

#### Scenario: A rejected port names the key the operator must edit

- **WHEN** validation refuses a host-mode port for a service whose configuration sets
  `external_port`
- **THEN** the error names that service's `external_port` config path, not its `port` path
- **AND** when the same service sets `port` only, the error names the `port` path, so the
  named key is in every case the key whose value was validated
