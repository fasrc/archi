## 1. Fix the broken repo-path discovery

- [x] 1.1 In `src/cli/utils/service_builder.py`, remove the `from src.cli.utils._repository_info import REPO_PATH` line and the `repo_path = REPO_PATH` assignment.
- [x] 1.2 Add a module-level helper `_discover_repo_path() -> Path` that walks `Path(__file__).resolve().parents` looking for a directory containing `pyproject.toml`; raises `click.ClickException` with a `--dev`-specific message if none is found.
- [x] 1.3 Call `_discover_repo_path()` from inside the `if dev_mode:` branch and assign its result to `repo_path`.
- [x] 1.4 Change `DeploymentPlan.__init__`'s `repo_path: str = ""` to `repo_path: Path = Path("")` and update `to_template_vars()` to emit `str(self.repo_path)` so the template renders cleanly.

## 2. Broaden byte-code suppression to all dev-mounted services

- [x] 2.1 In `src/cli/templates/base-compose.yaml`, add a new Jinja macro `dev_env()` that emits `PYTHONDONTWRITEBYTECODE: 1` (and any other dev-only env vars) when `dev_mode` is true and nothing otherwise.
- [x] 2.2 Remove the existing inline `PYTHONDONTWRITEBYTECODE` block from the `chatbot` service's `environment:` section.
- [x] 2.3 Add `{{ dev_env() }}` inside the `environment:` block of every service whose `volumes:` block calls `dev_src_mount()` or `dev_agents_mount(...)`: `data-manager`, `chatbot`, `grader`, `piazza`, `mattermost`, `redmine`, `mailbox`, `benchmark`.

## 3. Add unit tests

- [x] 3.1 Create `tests/unit/test_dev_mode_compose_render.py` that loads `base-compose.yaml` via Jinja, renders it twice (once with `dev_mode=True`, once with `dev_mode=False`), and asserts on the parsed-YAML structure: dev mounts present iff `dev_mode`, `PYTHONDONTWRITEBYTECODE` set on every dev-mounted service iff `dev_mode`, baseline service set unchanged.
- [x] 3.2 Create `tests/unit/test_deployment_plan_dev_mode.py` that calls `ServiceBuilder.build_compose_config(name="t", verbosity=0, base_dir=Path("/tmp/x"), enabled_services=["chatbot"], dev=True)` (with a monkeypatched `_discover_repo_path` returning `Path("/REPO")`) and asserts `plan.to_template_vars()["dev_mode"] is True` and `plan.to_template_vars()["repo_path"] == "/REPO"`. Repeat without `dev=True` and assert `dev_mode is False` and `repo_path == ""`.
- [x] 3.3 Create `tests/unit/test_cli_create_dev_smoke.py` that builds a minimal config fixture, runs `CliRunner().invoke(create, ["--dev", "--dry", "-n", "t", "-c", str(cfg), "-e", str(env_file), "--services", "chatbot", "--hostmode"])`, asserts `result.exit_code == 0` and `"DEV MODE" in result.output`. Monkeypatch `_discover_repo_path` so the test does not depend on the executing checkout.
- [x] 3.4 Run `python -m pytest tests/unit/test_dev_mode_compose_render.py tests/unit/test_deployment_plan_dev_mode.py tests/unit/test_cli_create_dev_smoke.py -v` locally and confirm all pass.

## 4. Verify and ship

- [x] 4.1 From a clean checkout, run `archi create --dev --dry -n smoke -c <real-config> -e <real-env> --services chatbot --hostmode` and confirm the rendered compose contains the expected bind mounts and `PYTHONDONTWRITEBYTECODE` lines on every dev-mounted service.
- [x] 4.2 Run the same command without `--dev` and diff the rendered compose against a pre-change baseline; confirm parsed-YAML structures match.
- [x] 4.3 Push the commits to `feat/dev-mode-mounts` so PR #3 picks them up automatically. Confirm the `pr-preview.yml` `unit-tests` job runs the three new test files and passes.
