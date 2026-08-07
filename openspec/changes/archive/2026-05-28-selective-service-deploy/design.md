## Context

Archi deploys code through a 4-step pipeline: `repo/src/` → `shutil.copytree` → `~/.archi/.../archi_code/` → Dockerfile `COPY` → `/root/archi/src/` + `pip install .` → `/usr/local/.../site-packages/src/`. At runtime, Python resolves imports from `/root/archi/src/` (via CWD `''` on `sys.path`), not site-packages. Flask templates and static files also resolve from `/root/archi/src/` via hardcoded config paths.

This means the live code path is always `/root/archi/src/`, making it a natural mount point for development.

Key files:
- `src/cli/cli_main.py` — `archi create` entry point
- `src/cli/managers/templates_manager.py` — `prepare_deployment_files()`, `copy_source_code()`
- `src/cli/templates/base-compose.yaml` — compose template with per-service volume definitions
- `src/cli/templates/dockerfiles/Dockerfile-chat` — image build with `COPY archi_code src` + `pip install .`

## Goals / Non-Goals

**Goals:**
- Code and agent prompt changes take effect on container restart with no redeploy
- Single `--dev` flag on `archi create` — no new commands
- Zero impact when `--dev` is omitted — production behavior unchanged

**Non-Goals:**
- Hot-reload without restart (Flask debug reloader may provide this as a bonus, but not a requirement)
- Dev mode for config YAML changes (template rendering is a real transformation, redeploy is correct)
- Changes to the Dockerfile or pip install process
- Selective per-service restart tooling (just use `docker restart`)

## Decisions

### 1. Mount repo/src/ over /root/archi/src/

**Choice:** Bind-mount the repo's `src/` directory directly to `/root/archi/src/` in the container.

**Why over PYTHONPATH approach:** `/root/archi/src/` is already the winning import path (CWD trick) AND the Flask template/static path. A single mount at this location covers both Python imports and Flask file serving. No `PYTHONPATH` manipulation needed.

**Why over site-packages overlay:** Site-packages path is Python-version-specific (`/usr/local/lib/python3.10/...`), fragile across base image upgrades. The `/root/archi/src/` path is controlled by the Dockerfile and stable.

**Alternatives considered:**
- `pip install -e .` in container — requires the repo to be mounted at build context, complicates Dockerfile, doesn't solve the deploy-copy chain.
- `docker cp` script — manual, fragile, doesn't survive container restart, still requires knowing internal paths.

### 2. Mount repo agents directly

**Choice:** In dev mode, mount `repo/config/agents/` → `/root/archi/agents/` instead of the deploy-copied `~/.archi/.../data/agents/`.

**Why:** Agent specs are read as plain markdown at runtime. No transformation. The deploy copy is pure overhead during development.

### 3. Pass repo_path through compose template

**Choice:** The CLI resolves the absolute path to the repo root and passes it as a template variable to `base-compose.yaml`. The compose template uses it for conditional volume mounts.

**Why:** The compose file needs absolute host paths for bind mounts. The CLI already knows the repo root via `_repository_info.REPO_PATH`.

### 4. Keep pip install in Dockerfile

**Choice:** Do not skip `pip install .` even in dev mode.

**Why:** The pip install resolves dependencies (langchain, flask, psycopg2, etc.) into site-packages. The mount only shadows the `src` package, not its dependencies. Skipping pip install would break imports of third-party libraries.

## Risks / Trade-offs

- **[Risk] Dependency drift** — If the repo adds a new dependency to `pyproject.toml`, the container's pip install won't have it until an image rebuild. → **Mitigation:** This is already the case for production. Dev mode doesn't make it worse. User runs `archi create --dev -f` to rebuild.

- **[Risk] File permission issues** — Host UID may differ from container UID. → **Mitigation:** Mount as read-only (`:ro`) for source code. Agent specs may need read-write if the UI edits them; keep current behavior.

- **[Risk] Stale .pyc files** — Container may have cached bytecode from the pip-installed source. Mounting live source could cause Python to use stale `.pyc` if mtime checks fail. → **Mitigation:** Python 3.10+ checks mtime by default. If issues arise, set `PYTHONDONTWRITEBYTECODE=1` in dev mode.

- **[Trade-off] Config changes still need redeploy** — Config goes through Jinja2 rendering, so live-mounting the YAML doesn't help. This is acceptable — config changes are infrequent compared to code changes.
