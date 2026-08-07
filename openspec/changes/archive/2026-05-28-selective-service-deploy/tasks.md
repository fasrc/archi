## 1. CLI Flag

- [x] 1.1 Add `--dev` click option to `archi create` in `src/cli/cli_main.py`
- [x] 1.2 Pass `dev_mode` and `repo_path` (from `_repository_info.REPO_PATH`) through to `ServiceBuilder.build_compose_config()` and into the template context

## 2. Compose Template

- [x] 2.1 Add `dev_mode` and `repo_path` as template variables in `src/cli/templates/base-compose.yaml`
- [x] 2.2 Add conditional source code volume mount (`{{ repo_path }}/src:/root/archi/src`) to each application service (chatbot, data-manager, grader, piazza, mattermost, redmine, mailbox, benchmarks) gated on `{% if dev_mode %}`
- [x] 2.3 Add conditional agent spec volume mount (`{{ repo_path }}/config/agents:/root/archi/agents`) to chatbot service, replacing the deploy-copied `./data/agents` mount when in dev mode

## 3. Template Renderer

- [x] 3.1 Update `TemplateManager._render_compose_file()` in `src/cli/managers/templates_manager.py` to include `dev_mode` and `repo_path` in the Jinja2 render context (handled via `DeploymentPlan.to_template_vars()` — no changes needed in templates_manager.py)

## 4. Verification

- [x] 4.1 Run `archi create --dev --dry-run` and verify the rendered compose.yaml contains the dev volume mounts with correct absolute paths
- [x] 4.2 Run `archi create --dev` (full deploy), edit a Python file in repo, restart the chatbot container, and confirm the change takes effect
- [x] 4.3 Run `archi create` (without --dev) and verify no dev mounts appear in compose.yaml — production behavior unchanged
