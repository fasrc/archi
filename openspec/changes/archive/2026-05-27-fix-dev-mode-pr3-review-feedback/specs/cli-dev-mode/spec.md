## ADDED Requirements

### Requirement: Repo path discovery for dev mode

When `--dev` is passed to `archi create`, the CLI SHALL resolve the absolute path of the archi git checkout that is hosting the running CLI process, and SHALL embed that path into the rendered compose template's bind-mount sources. The discovery MUST NOT depend on any module that does not exist in the checkout. If the repo root cannot be located, the CLI MUST raise a `click.ClickException` with an actionable message instead of crashing with `ModuleNotFoundError` or surfacing a stack trace.

#### Scenario: Repo path resolves from installed source tree

- **WHEN** `archi create --dev ...` is invoked from a checkout that contains a `pyproject.toml` at its root
- **THEN** the rendered compose file's chatbot service contains a `volumes:` entry of the form `<repo_root>/src:/root/archi/src`, where `<repo_root>` is the absolute path of the directory holding `pyproject.toml`

#### Scenario: Missing repo root fails fast

- **WHEN** `archi create --dev ...` is invoked from a process whose source tree cannot be traced back to a `pyproject.toml` (e.g., a wheel install with no nearby checkout)
- **THEN** the CLI exits with a `click.ClickException` whose message names `--dev` and instructs the operator to run from a git checkout
- **AND** no compose file is written

### Requirement: Dev-mode warning at create time

When `--dev` is passed, the CLI SHALL print a single yellow warning line on stdout before any deployment work begins, explaining that repo source will be bind-mounted and that dev mode is not suitable for production. The warning MUST be emitted exactly once per invocation.

#### Scenario: Warning prints for --dev

- **WHEN** `archi create --dev ...` is invoked
- **THEN** stdout contains the substring `DEV MODE` and the substring `Do NOT use on a production deployment` before any other deployment log line

#### Scenario: No warning without --dev

- **WHEN** `archi create ...` is invoked without `--dev`
- **THEN** stdout does not contain the substring `DEV MODE`

### Requirement: Byte-code suppression for dev-mounted services

Every compose service that receives the dev-mode `src/` bind mount SHALL also receive `PYTHONDONTWRITEBYTECODE=1` in its environment when `dev_mode` is true, to prevent the container from writing `.pyc` files into the host repo checkout. This MUST be enforced through the Jinja macros that emit the dev mounts so the two cannot drift apart.

#### Scenario: All dev-mounted services suppress .pyc

- **WHEN** the compose template is rendered with `dev_mode=True` and every application service enabled
- **THEN** each rendered service that contains a bind-mount line ending in `:/root/archi/src` also contains the environment line `PYTHONDONTWRITEBYTECODE: 1`

#### Scenario: Non-dev services do not set the env var

- **WHEN** the compose template is rendered with `dev_mode=False`
- **THEN** no service block contains the `PYTHONDONTWRITEBYTECODE` environment line

### Requirement: Compose template is byte-identical without --dev

The introduction of dev mode MUST be additive. When `dev_mode` is false the rendered compose YAML SHALL be byte-identical to what the template produced before dev mode existed, with the sole exception of whitespace-only differences that do not change the parsed YAML.

#### Scenario: Baseline render is preserved

- **WHEN** the compose template is rendered with `dev_mode=False`
- **THEN** parsing the result with PyYAML yields a structure equal to the structure parsed from the pre-dev-mode baseline (same services, same volumes, same environment keys)

### Requirement: DeploymentPlan exposes dev-mode template variables

`DeploymentPlan.to_template_vars()` SHALL expose `dev_mode: bool` and `repo_path: str` keys to Jinja. When `--dev` is passed through `ServiceBuilder.build_compose_config(**other_flags)`, the returned plan MUST carry `dev_mode=True` and a non-empty absolute `repo_path`. When `--dev` is absent, the plan MUST carry `dev_mode=False` and an empty `repo_path`.

#### Scenario: dev=True propagates to template vars

- **WHEN** `ServiceBuilder.build_compose_config(..., dev=True)` is called inside a git checkout
- **THEN** `plan.to_template_vars()["dev_mode"]` is `True`
- **AND** `plan.to_template_vars()["repo_path"]` is an absolute path string pointing at the checkout root

#### Scenario: dev defaulted off

- **WHEN** `ServiceBuilder.build_compose_config(...)` is called without `dev` in `other_flags`
- **THEN** `plan.to_template_vars()["dev_mode"]` is `False`
- **AND** `plan.to_template_vars()["repo_path"]` is `""`

### Requirement: Dev-mode behavior is covered by CI tests

The dev-mode capability SHALL have unit tests under `tests/unit/` that exercise (a) compose-template rendering with `dev_mode` both true and false, (b) `DeploymentPlan.to_template_vars()` for both modes, and (c) a `click.testing.CliRunner` invocation of `archi create --dev --dry-run ...`. These tests MUST be picked up by the existing `pr-preview.yml` `unit-tests` job without workflow changes.

#### Scenario: CI runs the new tests

- **WHEN** a PR touching dev-mode files is opened against `dev`
- **THEN** the `pr-preview.yml` `unit-tests` job executes the new `test_dev_mode_compose_render.py`, `test_deployment_plan_dev_mode.py`, and `test_cli_create_dev_smoke.py` files
- **AND** the job fails if any of those tests fail
