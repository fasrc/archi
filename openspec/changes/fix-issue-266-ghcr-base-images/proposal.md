## Why

Service images cannot be built on a clean host. All 15 service Dockerfile templates start
`FROM docker.io/a2rchi/a2rchi-{python,pytorch}-base:latest` — an upstream-owned, floating
tag this fork does not control. That image ships Python 3.10.20, while `pyproject.toml:5`
declares `requires-python = ">=3.11"`, so `pip install .` fails in every service build on
any host that does not already hold a locally built base image (`fasrc/archi#266`).

The failure is invisible to CI: `.github/workflows/pr-preview.yml` builds the base image
fresh before the service images, so no CI job ever resolves the Docker Hub tag. Only a real
operator on a clean host meets the defect, and the error they get comes from `pip`, which
names neither the base image nor the version conflict.

## What Changes

- Repoint all 15 service Dockerfile templates from `docker.io/a2rchi/*:latest` to
  `ghcr.io/fasrc/*:dev-4314ac4` — images this fork publishes, at a pinned tag. This is the
  same rewrite `.github/workflows/test-and-build-tag.yml:154` already applies at tag time,
  performed by the existing `scripts/dev/update_service_base_images.py`.
- Guard the pin with a unit test, so a regression to `docker.io/a2rchi/` or to a floating
  `latest` a2rchi tag fails the gate and names the offending file.
- Add a base-image preflight to `archi create` that establishes every required base image is
  present on the host — pulling it if absent — and refuses the deployment with a diagnostic
  naming the image and the matching remedy when it cannot. Each image is then checked against
  the `requires-python` floor, so the interpreter mismatch is caught on a clean host and not
  only on one that happens to hold a cached image.
- **The preflight runs above the `--force` teardown**, not merely before compose. The
  existing `cli-create-preflight` contract states that no destructive step may precede a
  step that can refuse the deployment. A preflight is by definition such a step, so placing
  it just before `DeploymentManager.start_deployment` (`cli_main.py:320`) would put it
  *after* `remove_existing_deployment` (`cli_main.py:278`) and regress that contract.

## Capabilities

### New Capabilities
- `service-base-images`: which registry and tag the service Dockerfile templates reference,
  and the invariant that the reference is fork-controlled and pinned rather than upstream
  and floating.

### Modified Capabilities
- `cli-create-preflight`: adds a requirement that `archi create` verifies base-image
  availability before the teardown, and that an unavailable base image produces a
  diagnostic naming the image and the operator remedy.

## Impact

- **Templates (15 files):** `src/cli/templates/dockerfiles/Dockerfile-{benchmarks,chat,
  data-manager,mailbox,mattermost,piazza,redmine}` and the eight `*-gpu` and `grader`
  variants. Not touched: `Dockerfile-base`, `Dockerfile-base-gpu`, `Dockerfile-postgres`,
  `Dockerfile-grafana`, and the two `base-*-image/Dockerfile` files.
- **New module:** `src/cli/managers/base_image_preflight.py`, holding the pure logic behind
  a mockable seam for the docker call.
- **Call site:** `src/cli/cli_main.py` gains a thin call between the port check and
  `remove_existing_deployment`. `app.py`-style diff-coverage rules apply, so the logic stays
  in the helper module.
- **Tests:** `tests/unit/test_python_version_declaration.py` extended; new unit tests for
  the preflight module.
- **Operator-facing:** the `fasrc` ghcr packages are `internal`, so a pull requires one
  `docker login ghcr.io` with a **classic** PAT carrying `read:packages`. Fine-grained PATs
  have no Packages permission and cannot be used (`fasrc/archi#322`). Documented, not
  automated: archi never stores, reads, or writes a registry credential.
- **Not affected:** CI, which builds its own base image and authenticates with
  `GITHUB_TOKEN`.
