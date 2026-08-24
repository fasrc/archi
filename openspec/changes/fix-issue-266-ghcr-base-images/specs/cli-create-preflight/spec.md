## ADDED Requirements

### Requirement: Base-image availability is established before the teardown

`archi create` SHALL establish that every base image its enabled services build on is present on the host, and SHALL do so before `remove_existing_deployment` runs.

This requirement is an instance of the existing rule that no destructive step may precede a
step that can refuse the deployment. A base-image check can refuse, so it belongs above the
teardown — not merely above `DeploymentManager.start_deployment`, which sits below it. A
`--force` create that was always going to fail for want of a base image must not first
destroy a working deployment.

"Present on the host" is the standard, not "resolvable in a registry". Reachability proves
that a tag resolves; it does not prove the image behind it is usable. An image that is
absent locally is therefore pulled here, while refusing is still free, rather than left for
compose to pull after the teardown. This is not extra work — compose pulls the same image
moments later — it is the same work moved to where its failure is recoverable.

The base references are read from the `FROM` lines of the templates the deployment will
actually use, never inferred from the GPU flag. `Dockerfile-grader` is a non-GPU service
that builds on the pytorch base, so a rule of the form "GPU implies pytorch, otherwise
python" checks an image the deployment does not use and skips one it does.

No reference is exempt from the checked set.

#### Scenario: Forced re-create whose base image cannot be obtained

- **WHEN** `archi create --force` is invoked against an existing deployment, and a required base image is absent locally and cannot be pulled
- **THEN** the command exits non-zero
- **AND** `remove_existing_deployment()` is never called
- **AND** the existing deployment directory and its contents are left intact
- **AND** compose is never invoked

#### Scenario: Base image absent locally but available from the registry

- **WHEN** a required base image is absent from the host and its registry serves it
- **THEN** it is pulled before the teardown
- **AND** the deployment proceeds

#### Scenario: The checked set follows the templates, not the GPU flag

- **WHEN** the enabled services include `grader` and no GPU is requested
- **THEN** the pytorch base image is among those checked, because that is what `Dockerfile-grader` names
- **AND** an image no enabled service builds on is not checked

#### Scenario: A locally built base reference that is not present is refused

- **WHEN** a required base reference carries a `localhost/` prefix and no such image is present on the host
- **THEN** the command exits non-zero before the teardown
- **AND** the error directs the operator to build the base image

A `localhost/` prefix is the tag `scripts/dev/build_docker_images.sh` applies to a locally
built base. It is a registry-style reference, not evidence that the image exists: a fresh or
pruned daemon resolves it to nothing, and no registry can supply it. Treating the prefix as
self-evidently satisfied would skip the check precisely when it is needed.

### Requirement: A present base image is not re-fetched

The preflight SHALL treat a base image already present on the host as available, and SHALL NOT contact a registry for it.

A host that has already pulled or built its base images is fully provisioned. Requiring it to
reach a registry it does not need would refuse deployments that would otherwise succeed,
including every offline rebuild.

#### Scenario: Base image already present on the host

- **WHEN** every required base image is present locally
- **THEN** no registry request is made
- **AND** the preflight passes
- **AND** the deployment proceeds

### Requirement: Every base image is checked against the declared Python floor

The preflight SHALL compare each required base image's Python version against `requires-python` in `pyproject.toml`, and SHALL refuse the deployment when the image is below the floor or when its version cannot be determined.

The comparison applies to every base image without exception, because by this point each one
is present on the host. A version check that ran only for images that happened to be cached
would be absent exactly on a clean host, which is the case `fasrc/archi#266` was filed about.

A version that cannot be read or parsed is also a refusal, with its own diagnostic. Passing
it would convert an unknown compatibility result into permission to destroy a working
deployment, which is the precise failure this requirement exists to prevent. The false-refusal
cost is negligible: the probe runs a container, and the build runs containers too, so a host
that cannot run the probe cannot complete the build either.

#### Scenario: Base image whose Python is below the declared floor

- **WHEN** a required base image reports a Python version below `requires-python` in `pyproject.toml`
- **THEN** the command exits non-zero before the teardown
- **AND** the error names both the version the image reports and the declared floor

#### Scenario: Freshly pulled base image is checked too

- **WHEN** a required base image was absent and had to be pulled
- **THEN** its Python version is compared against the floor as well
- **AND** a version below the floor refuses the deployment before the teardown

#### Scenario: Base image whose Python version cannot be read

- **WHEN** the version probe against a present base image fails or returns output that cannot be parsed
- **THEN** the command exits non-zero before the teardown
- **AND** the error names the image reference and states that its Python version could not be determined
- **AND** `remove_existing_deployment()` is never called

### Requirement: An unavailable base image is diagnosed by cause

When the preflight refuses, the error SHALL name the base image reference and SHALL state the remedy that matches the cause of the failure.

The remedies differ and are not interchangeable, so one collapsed message is wrong for most
of the cases:

- Authentication refused: log in to the registry. The message MUST state that the token has
  to be a **classic** personal access token carrying `read:packages`, and MUST mention SSO
  authorization. A fine-grained token carries no Packages permission at all and fails with
  an indistinguishable refusal, so an operator told only "log in" will retry with the wrong
  credential class and get the same error.
- Manifest or tag unknown: the pinned tag is absent, so the pin is stale or the tag was
  deleted. Logging in cannot fix this.
- Registry unreachable: a network or registry fault, with nothing to change in archi.
- A `localhost/` base that is absent: build the base image.
- The pull ran out of disk: free space and retry. This is called out separately because the
  generic failure text would send an operator with a full disk to `docker login`.

The login command named MUST match the container tool the deployment itself uses, so a
`--podman` deployment is not told to run `docker login`.

#### Scenario: Registry refuses authentication

- **WHEN** the registry refuses the pull as unauthorized
- **THEN** the error names the image reference and the registry
- **AND** it states that a classic personal access token with `read:packages` is required
- **AND** it mentions SSO authorization
- **AND** it names the login command for the deployment's own container tool

#### Scenario: The pinned tag no longer exists

- **WHEN** the registry answers that the manifest or tag is unknown
- **THEN** the error identifies the pin as stale or deleted
- **AND** it does not tell the operator to log in

#### Scenario: The pull runs out of disk

- **WHEN** the pull fails because the host is out of disk space
- **THEN** the error identifies disk exhaustion as the cause
- **AND** it does not tell the operator to log in

#### Scenario: Podman deployment is given a podman remedy

- **WHEN** the deployment runs under `--podman` and authentication is refused
- **THEN** the login command in the error names `podman`, not `docker`

### Requirement: A dry run never reports readiness it did not verify

`archi create --dry` SHALL NOT pull any image, SHALL refuse on any preflight cause it can establish without pulling, and SHALL mark the base images as not verified — naming the reason — whenever it cannot determine their state. A real create whose container runtime cannot be invoked SHALL be refused.

Dry runs mirror the refusals a real run would make on the same inputs, which is what the rest
of this capability already specifies. The failure mode this requirement closes is subtler than
a missing check: a dry run that silently succeeds because it *could not look* reports a ready
host to an operator whose real create will refuse moments later. Silence and a logged note are
the same defect. The contract is therefore about what the dry run asserts, not only about what
it inspects.

A dry run continues to require no container runtime (`src/cli/cli_main.py:155-160`). Refusing
because it cannot look would make `--dry` unusable on exactly those hosts, so the exit status
stays 0 and the summary carries the unverified marker instead.

The version comparison requires the image on the host, so it never runs under `--dry`.

#### Scenario: Dry run pulls nothing

- **WHEN** `archi create --dry` is invoked and a required base image is absent locally but reachable
- **THEN** no image is pulled
- **AND** the command completes and prints its dry-run summary

#### Scenario: Dry run refuses what the real create would refuse

- **WHEN** `archi create --dry --force` is invoked and a required base image's registry refuses authorization
- **THEN** the command exits non-zero with the same cause the real create would report
- **AND** the existing deployment directory is left intact
- **AND** no image is pulled

#### Scenario: Dry run on a host with no runtime

- **WHEN** `archi create --dry` is invoked on a host with no container runtime
- **THEN** the command completes and exits 0
- **AND** the dry-run summary marks the base images as not verified
- **AND** the summary names the absent container runtime as the reason

#### Scenario: Dry run whose reachability probe is unsupported

- **WHEN** `archi create --dry` is invoked on a host whose container tool does not support the reachability probe, and a required base image is absent locally
- **THEN** the command exits 0
- **AND** the dry-run summary marks the base images as not verified
- **AND** the summary names the unsupported probe as the reason

This scenario is separate from the one above because the two reasons reach the unverified
state by different routes, and an implementation can easily handle one and silently succeed on
the other.

#### Scenario: Dry run cannot judge what it did not fetch

- **WHEN** `archi create --dry` is invoked and a required base image is absent locally but reachable
- **THEN** no Python-version comparison is made for that image
- **AND** the absence alone does not refuse the dry run

#### Scenario: Real create whose runtime cannot be invoked

- **WHEN** `archi create --podman` is invoked on a host where podman cannot be invoked
- **THEN** the command exits non-zero before the teardown
- **AND** the existing deployment directory is left intact
