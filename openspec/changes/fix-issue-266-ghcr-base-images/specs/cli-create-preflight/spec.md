## ADDED Requirements

### Requirement: Base-image availability is checked before the teardown

`archi create` SHALL verify that every base image its enabled services build on is either present locally or reachable from its registry, and SHALL perform that check before `remove_existing_deployment` runs.

This requirement is an instance of the existing rule that no destructive step may precede a
step that can refuse the deployment. A base-image check can refuse, so it belongs above the
teardown — not merely above `DeploymentManager.start_deployment`, which sits below it. A
`--force` create that was always going to fail for want of a base image must not first
destroy a working deployment.

The base references are read from the `FROM` lines of the templates the deployment will
actually use, never inferred from the GPU flag. `Dockerfile-grader` is a non-GPU service
that builds on the pytorch base, so a rule of the form "GPU implies pytorch, otherwise
python" checks an image the deployment does not use and skips one it does.

References carrying a `localhost/` prefix are excluded, since a locally built base image is
by construction the "present locally" case.

#### Scenario: Forced re-create whose base image cannot be obtained

- **WHEN** `archi create --force` is invoked against an existing deployment, and a required base image is neither present locally nor reachable from its registry
- **THEN** the command exits non-zero
- **AND** `remove_existing_deployment()` is never called
- **AND** the existing deployment directory and its contents are left intact
- **AND** compose is never invoked

#### Scenario: The checked set follows the templates, not the GPU flag

- **WHEN** the enabled services include `grader` and no GPU is requested
- **THEN** the pytorch base image is among those checked, because that is what `Dockerfile-grader` names
- **AND** an image no enabled service builds on is not checked

#### Scenario: A locally built base image needs no registry

- **WHEN** a required base reference carries a `localhost/` prefix
- **THEN** no registry request is made for it
- **AND** the preflight does not refuse the deployment on its account

### Requirement: A present base image satisfies the check without a registry request

The preflight SHALL treat a base image already present on the host as available, and SHALL NOT contact the registry for it.

A host that has already pulled or built its base images is fully provisioned. Requiring it
to authenticate to a registry it does not need would refuse deployments that would otherwise
succeed, including every offline rebuild.

Only in this case, where the image is already on the host and the check is nearly free, is
the image's Python version additionally compared against the `requires-python` floor. A
version that cannot be read or parsed is an explicit unknown outcome that passes with a
logged note. Failing a deployment because a probe did not work is worse than the mismatch
the probe was looking for.

#### Scenario: Base image already present on the host

- **WHEN** every required base image is present locally
- **THEN** no registry request is made
- **AND** the preflight passes
- **AND** the deployment proceeds

#### Scenario: Present base image whose Python is below the declared floor

- **WHEN** a locally present base image reports a Python version below `requires-python` in `pyproject.toml`
- **THEN** the command exits non-zero
- **AND** the error names both the version the image reports and the declared floor

#### Scenario: Present base image whose Python version cannot be read

- **WHEN** the version probe against a locally present base image fails or returns output that cannot be parsed
- **THEN** the preflight passes
- **AND** the outcome is recorded as unknown rather than as a mismatch or a crash

### Requirement: An unavailable base image is diagnosed by cause

When the preflight refuses, the error SHALL name the base image reference and SHALL state the remedy that matches the cause of the failure.

The remedies differ and are not interchangeable, so one collapsed message is wrong for at
least two of the three cases:

- Authentication refused: log in to the registry. The message MUST state that the token has
  to be a **classic** personal access token carrying `read:packages`, and MUST mention SSO
  authorization. A fine-grained token carries no Packages permission at all and fails with
  an indistinguishable refusal, so an operator told only "log in" will retry with the wrong
  credential class and get the same error.
- Manifest unknown: the pinned tag is absent, so the pin is stale or the tag was deleted.
  Logging in cannot fix this.
- Registry unreachable: a network or registry fault, with nothing to change in archi.

The login command named MUST match the container tool the deployment itself uses, so a
`--podman` deployment is not told to run `docker login`.

#### Scenario: Registry refuses authentication

- **WHEN** the registry refuses the manifest request as unauthorized
- **THEN** the error names the image reference and the registry
- **AND** it states that a classic personal access token with `read:packages` is required
- **AND** it mentions SSO authorization
- **AND** it names the login command for the deployment's own container tool

#### Scenario: The pinned tag no longer exists

- **WHEN** the registry answers that the manifest is unknown
- **THEN** the error identifies the pin as stale or deleted
- **AND** it does not tell the operator to log in

#### Scenario: Podman deployment is given a podman remedy

- **WHEN** the deployment runs under `--podman` and authentication is refused
- **THEN** the login command in the error names `podman`, not `docker`

### Requirement: The preflight never blocks a deployment it cannot judge

The preflight SHALL pass, with a logged note, whenever it cannot reach a verdict.

The check is defense in depth against a known failure. It is not an authority on whether the
build can succeed, and compose remains the real arbiter. Two cases are explicitly not
refusals: no container runtime is available, and the availability probe is unsupported or
returns a result the preflight does not recognise.

`--dry` deliberately requires no container runtime, and must not start requiring one. Where
a runtime does exist the preflight still runs under `--dry`, since it is read-only and
improves the dry run's fidelity at no cost.

#### Scenario: No container runtime available

- **WHEN** no container runtime can be invoked
- **THEN** the preflight is skipped with a logged note
- **AND** the deployment is not refused on its account

#### Scenario: Dry run on a host with no runtime

- **WHEN** `archi create --dry` is invoked on a host with no container runtime
- **THEN** the command completes and prints its dry-run summary
- **AND** the preflight does not cause a failure

#### Scenario: The availability probe is unsupported

- **WHEN** the probe used to test registry reachability is unsupported by the host's container tool
- **THEN** the preflight passes with a logged note
- **AND** the deployment proceeds to compose
