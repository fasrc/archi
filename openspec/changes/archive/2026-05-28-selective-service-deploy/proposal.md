## Why

Developing for archi is painful. Every code change requires a full `archi create -f` cycle that tears down all containers, copies source code through three locations (repo → `~/.archi/` → Docker COPY → pip install), rebuilds images, and restarts everything. A single-line Python fix takes minutes to test. This blocks iteration on the chatbot, agent prompts, and OpenAI-compatible API during active development.

## What Changes

- Add a `--dev` flag to `archi create` that bind-mounts the repo's source code and agent specs directly into service containers, bypassing the copy-and-bake pipeline for code changes.
- In dev mode, editing source files or agent prompts in the repo takes effect on container restart — no redeploy, no image rebuild.
- Config changes (YAML) still require a redeploy since they go through Jinja2 template rendering. This is intentional.

## Capabilities

### New Capabilities
- `dev-mode-mounts`: Add `--dev` flag to `archi create` that injects volume mounts for live source code and agent specs from the repo into containers, enabling restart-only development workflow.

### Modified Capabilities

## Impact

- **CLI**: `archi create` gains a `--dev` flag. No breaking changes — omitting the flag preserves current behavior.
- **Compose template**: `base-compose.yaml` gains conditional volume mounts gated on `dev_mode`.
- **TemplateManager**: Must pass `dev_mode` and `repo_path` through to the compose renderer.
- **No Dockerfile changes**: The existing `COPY` + `pip install` still runs (needed for dependencies), but the volume mount shadows the copied source at runtime.
- **No Python path changes**: `/root/archi/src/` already wins for imports via CWD-on-sys.path. The mount just replaces the stale COPY'd source with the live repo.
