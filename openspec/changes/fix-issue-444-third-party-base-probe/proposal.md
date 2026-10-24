## Why

`archi create --force` and `archi evaluate --force` run a base-image preflight before they
tear down an existing deployment (fasrc/archi#287, #394). The preflight probes only the two
`a2rchi-*-base` images. It never probes the third-party images that two service templates
build FROM (fasrc/archi#444):

- `src/cli/templates/dockerfiles/Dockerfile-postgres:8` — `FROM docker.io/pgvector/pgvector:pg17`
- `src/cli/templates/dockerfiles/Dockerfile-grafana:4` — `FROM docker.io/grafana/grafana-enterprise:10.2.0`

`NON_SERVICE_TEMPLATES` (`src/cli/managers/base_image_preflight.py:189-196`) excludes both
templates, so `service_templates` (`:476`), `required_base_images` (`:675`) and
`required_base_image_names` (`:707`) never return them. Every `evaluate` plan enables
postgres (`src/cli/cli_main.py:855`), and every `create` plan with chatbot, grader, piazza,
mattermost or redmine resolves postgres as a dependency. A host that cannot obtain the
pgvector image therefore passes the preflight, `--force` removes the working deployment,
and the build then fails.

The images cannot simply join the existing reference list: `run_preflight` (`:1018-1060`)
runs `check_python_floor` on every present image (`:1052-1053`), and a non-Python image
can never satisfy that check.

Anchors verified on `origin/dev` 2a92d3c3 on 2026-10-03.

## What Changes

- Add a reference type `BaseReference(image: str, check_floor: bool = True)`.
  `run_preflight` accepts plain strings (read as `check_floor=True`) and `BaseReference`
  values in one list, and decides all of them in one loop.
- When `check_floor` is false, `run_preflight` never calls `probe.python_version` and never
  calls `check_python_floor`. The availability outcome is the final outcome. On a dry run, a
  reachable floor-free image is AVAILABLE, because no version needs to be read.
- Add `THIRD_PARTY_BASE_TEMPLATES`: service name → template file (`postgres` →
  `Dockerfile-postgres`, `grafana` → `Dockerfile-grafana`). A new function reads each
  template's final-stage `FROM` and returns the references for the services that are
  enabled in the compose plan. A template with no readable `FROM` refuses, as the
  unresolved a2rchi path does.
- `enforce_base_images` appends those references with `check_floor=False` after the a2rchi
  references, before `run_preflight`, so the refusal still comes before any teardown.
- `compose_message` gives registry-neutral login text for an UNAUTHORIZED third-party
  image (the current text names the internal fasrc packages and a `read:packages` token).
- `NON_SERVICE_TEMPLATES`, `service_templates`, `two_image_rule_offenders` and
  `templates_missing_base_reference` do not change.
- `cli_main.py` does not change.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `cli-create-preflight`: the preflight also establishes the availability of the
  third-party base images that the planned services build FROM. Written as an `ADDED`
  requirement, because `fix-issue-394-evaluate-base-image-preflight` is still unarchived.

## Impact

- `src/cli/managers/base_image_preflight.py`
- `tests/unit/test_base_image_preflight.py`
- No change to `deploy/**`, `.github/workflows/**`, or the teardown order in `cli_main.py`.
