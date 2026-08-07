# dev-mode-mounts

## Purpose

Developing for archi is painful because every code change requires a full `archi create -f` cycle that tears down containers, copies source through multiple locations, rebuilds images, and restarts everything — turning a one-line Python fix into a multi-minute round trip. This capability adds a `--dev` flag to `archi create` that bind-mounts the repo's source code and agent specs directly into service containers, bypassing the copy-and-bake pipeline so that edits to Python files, Flask templates, or agent prompts take effect on a simple container restart. Config (YAML) changes still flow through Jinja2 rendering and require redeploy; this is intentional.

## Requirements

### Requirement: Dev mode flag on archi create
The `archi create` command SHALL accept a `--dev` flag that enables development volume mounts for all application service containers. When `--dev` is omitted, deployment behavior SHALL be identical to current behavior.

#### Scenario: Create deployment with dev mode
- **WHEN** user runs `archi create --name mybot --dev --config config.yaml --services chatbot --hostmode`
- **THEN** the generated `compose.yaml` SHALL include bind mounts from the repo's `src/` directory to `/root/archi/src/` for each application service container

#### Scenario: Create deployment without dev mode
- **WHEN** user runs `archi create --name mybot --config config.yaml --services chatbot --hostmode`
- **THEN** the generated `compose.yaml` SHALL NOT include any dev-mode bind mounts and behavior SHALL be unchanged from current

### Requirement: Source code mount in dev mode
In dev mode, each application service container SHALL have the repo's `src/` directory bind-mounted to `/root/archi/src/`, shadowing the Dockerfile COPY'd source. This mount SHALL cover both Python imports (resolved via CWD on sys.path) and Flask template/static file paths.

#### Scenario: Python code change takes effect on restart
- **WHEN** dev mode is enabled and user edits a Python file in `repo/src/`
- **THEN** restarting the container SHALL pick up the change without any deploy or image rebuild step

#### Scenario: Flask template change takes effect on restart
- **WHEN** dev mode is enabled and user edits a template in `repo/src/interfaces/chat_app/templates/`
- **THEN** restarting the container SHALL serve the updated template

### Requirement: Agent spec mount in dev mode
In dev mode, agent spec markdown files SHALL be mounted directly from the repo's `config/agents/` directory to `/root/archi/agents/`, replacing the deploy-copied mount.

#### Scenario: Agent prompt change takes effect on restart
- **WHEN** dev mode is enabled and user edits `config/agents/fasrc-cannon.md`
- **THEN** restarting the container SHALL use the updated agent prompt without any deploy step

### Requirement: Repo path resolution
The CLI SHALL resolve the absolute path to the repository root and pass it to the compose template renderer for use in bind mount paths. The repo path SHALL be determined from the existing `_repository_info.REPO_PATH` mechanism.

#### Scenario: Compose file contains correct host paths
- **WHEN** dev mode is enabled and the repo is at `/home/a2rchi/archi-openai-compat`
- **THEN** the generated `compose.yaml` SHALL contain bind mount sources using that absolute path
