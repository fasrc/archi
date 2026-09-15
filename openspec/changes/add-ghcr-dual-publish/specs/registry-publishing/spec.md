## ADDED Requirements

### Requirement: ghcr.io as additional publish target
CI workflows that build the base images SHALL publish them to `ghcr.io/fasrc/a2rchi-python-base` and `ghcr.io/fasrc/a2rchi-pytorch-base` in addition to (not replacing) the existing `docker.io/a2rchi/...` push targets. Image names SHALL retain the `a2rchi-` prefix across both registries.

#### Scenario: PR preview workflow publishes both registries when DockerHub secrets are present
- **WHEN** `pr-preview.yml` runs on a PR with both `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` configured as repository secrets
- **THEN** the workflow SHALL push the built base images to both `ghcr.io/fasrc/a2rchi-{python,pytorch}-base:pr-<N>` and `docker.io/a2rchi/a2rchi-{python,pytorch}-base:pr-<N>` and succeed

#### Scenario: Publish-base-images workflow publishes both registries when DockerHub secrets are present
- **WHEN** `publish-base-images.yml` runs on a push to `main` with both `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` configured
- **THEN** the workflow SHALL push to both `ghcr.io/fasrc/a2rchi-{python,pytorch}-base:main-<sha>` and `ghcr.io/fasrc/a2rchi-{python,pytorch}-base:latest`, AND push to the matching `docker.io/a2rchi/...` tags, and succeed

#### Scenario: Test-and-build-tag workflow publishes both registries for tagged releases
- **WHEN** `test-and-build-tag.yml` runs on a release tag push with both DockerHub secrets configured
- **THEN** the workflow SHALL push tagged + `latest` images to both registries

### Requirement: ghcr.io publishing uses GITHUB_TOKEN with no extra secrets
Publishing to ghcr.io SHALL authenticate using the workflow-auto-injected `GITHUB_TOKEN` with `permissions: packages: write` at the job level. No new repository or organization secrets SHALL be required for ghcr.io pushes.

#### Scenario: Fresh fork with no provisioned secrets can publish to ghcr
- **WHEN** a workflow runs on a fork (e.g. fasrc/archi) where neither `DOCKERHUB_USERNAME` nor `DOCKERHUB_TOKEN` is configured, but `GITHUB_TOKEN` is auto-injected by the runner
- **THEN** the workflow SHALL successfully push to `ghcr.io/fasrc/a2rchi-{python,pytorch}-base` and the job SHALL exit with success

### Requirement: Graceful skip of docker.io push when secrets are absent
The docker.io publish step SHALL be conditional on both `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` being non-empty. When either is empty or unset, the step SHALL log a single notice ("DockerHub credentials not configured; skipping docker.io publish") and exit with success without attempting a `docker login docker.io` or `docker push docker.io/...`. The overall job SHALL NOT fail solely because docker.io credentials are missing.

#### Scenario: Missing DOCKERHUB_USERNAME skips docker.io push gracefully
- **WHEN** a workflow runs with `DOCKERHUB_TOKEN` set but `DOCKERHUB_USERNAME` empty/unset
- **THEN** the docker.io publish step SHALL emit a "skipping" notice, perform no docker login or push to docker.io, exit with success, and the overall job SHALL succeed assuming the ghcr.io push succeeded

#### Scenario: Missing DOCKERHUB_TOKEN skips docker.io push gracefully
- **WHEN** a workflow runs with `DOCKERHUB_USERNAME` set but `DOCKERHUB_TOKEN` empty/unset
- **THEN** the docker.io publish step SHALL emit a "skipping" notice, perform no docker login or push to docker.io, exit with success, and the overall job SHALL succeed assuming the ghcr.io push succeeded

#### Scenario: Both DockerHub secrets missing still produces a green CI job
- **WHEN** a workflow runs on a fork with neither DockerHub secret configured
- **THEN** ghcr.io push SHALL succeed, docker.io publish step SHALL be skipped with notice, and the overall workflow SHALL conclude with success

### Requirement: ghcr.io packages are public on first publish
On the first publish of `ghcr.io/fasrc/a2rchi-python-base` and `ghcr.io/fasrc/a2rchi-pytorch-base`, the packages SHALL be configured with visibility = public. The base images SHALL remain pullable anonymously from ghcr.io without any `docker login` step.

#### Scenario: Anonymous docker pull from ghcr succeeds
- **WHEN** a user runs `docker pull ghcr.io/fasrc/a2rchi-python-base:latest` from a machine that has never run `docker login ghcr.io`
- **THEN** the pull SHALL succeed without authentication errors

### Requirement: Registry source switch supports ghcr
`scripts/dev/update_service_base_images.py` SHALL accept `--switch-source ghcr` as a valid registry source value, equivalent to `--switch-source dockerhub` and `--switch-source localhost` already supported. The `SOURCE_PREFIXES` mapping SHALL include the entry `"ghcr": "ghcr.io/fasrc/"`.

#### Scenario: archi create rewrites service Dockerfiles to pull from ghcr
- **WHEN** a user runs `archi create --base-image-source ghcr -n my-deploy ...` (or equivalent invocation that triggers `update_service_base_images.py --switch-source ghcr`)
- **THEN** the `FROM` line in every generated/installed service Dockerfile SHALL be rewritten from `docker.io/a2rchi/a2rchi-{python,pytorch}-base:<tag>` to `ghcr.io/fasrc/a2rchi-{python,pytorch}-base:<tag>`, preserving the tag

#### Scenario: Default behavior unchanged when --base-image-source is omitted
- **WHEN** a user runs `archi create` without `--base-image-source`
- **THEN** generated/installed service Dockerfiles SHALL continue to use `docker.io/a2rchi/a2rchi-{python,pytorch}-base:<tag>` as the `FROM` source, matching pre-change behavior

### Requirement: In-tree Dockerfile templates remain docker.io-defaulted
The 12 `src/cli/templates/dockerfiles/Dockerfile-*` files SHALL continue to have `FROM docker.io/a2rchi/a2rchi-{python,pytorch}-base:latest` as their checked-in default. This change SHALL NOT hardcode any `FROM ghcr.io/...` line in the repository.

#### Scenario: Repo grep confirms no hardcoded ghcr.io FROM lines
- **WHEN** a contributor runs `grep -rn '^FROM ghcr.io' src/cli/templates/dockerfiles/`
- **THEN** the search SHALL return zero results
