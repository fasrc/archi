# CLI Reference

The Archi CLI provides commands to create, manage, and monitor deployments.

## Installation

The CLI is installed automatically with `pip install -e .` from the repository root. Verify with:

```bash
which archi
```

---

## Commands

### `archi create`

Create a new Archi deployment.

```bash
archi create --name <name> --config <config.yaml> --env-file <secrets.env> --services <services> [OPTIONS]
```

**Required options:**

| Option | Description |
|--------|-------------|
| `--name`, `-n` | Name of the deployment |
| `--config`, `-c` | Path to YAML configuration file (repeatable for multiple files) |

**Recommended options:**

| Option | Description |
|--------|-------------|
| `--env-file`, `-e` | Path to the secrets `.env` file |
| `--services`, `-s` | Comma-separated list of services to enable (e.g., `chatbot,uploader`) |

**Optional flags:**

| Option | Description | Default |
|--------|-------------|---------|
| `--config-dir`, `-cd` | Directory containing configuration files | — |
| `--podman`, `-p` | Use Podman instead of Docker | Docker |
| `--gpu-ids` | GPU configuration: `all` or comma-separated IDs (e.g., `0,1`) | None |
| `--tag`, `-t` | Image tag for built containers | `2000` |
| `--hostmode` | Use host network mode for all services | Off |
| `--verbosity`, `-v` | Logging verbosity level (0=quiet, 4=debug) | `3` |
| `--force`, `-f` | Overwrite existing deployment if it exists | Off |
| `--dry`, `--dry-run` | Validate and show what would be created without deploying | Off |
| `--dev` | Enable dev mode: mount repo source and agents into containers for restart-only development | Off |

**Examples:**

```bash
# Basic deployment with Ollama
archi create -n my-archi -c config.yaml -e .secrets.env \
  --services chatbot --podman

# Full deployment with GPU and multiple services
archi create -n prod-archi -c config.yaml -e .secrets.env \
  --services chatbot,uploader,grafana \
  --gpu-ids all

# Dry run to validate configuration
archi create -n test -c config.yaml -e .secrets.env \
  --services chatbot --dry-run

# Dev mode: code changes take effect on container restart
archi create -n my-archi --dev -f -c config.yaml -e .secrets.env \
  --services chatbot --hostmode
```

**Notes:**

- The CLI checks that host ports are free before deploying. If a port is in use, adjust `services.*.external_port` in your config.
- The first deployment builds container images from scratch (may take several minutes). Subsequent deployments reuse images.
- Use `-v 4` for debug-level logging when troubleshooting.
- **Dev mode** (`--dev`): Bind-mounts the repo's `src/` and `config/agents/` directly into containers. After the initial deploy, edit source code or agent prompts and just `docker restart <container>` — no redeploy or image rebuild needed. Config YAML changes still require a redeploy.

---

### `archi delete`

Delete an existing deployment.

```bash
archi delete --name <name> [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--name`, `-n` | Name of the deployment to delete |
| `--rmi` | Also remove container images |
| `--rmv` | Also remove volumes |
| `--keep-files` | Keep deployment files on disk |
| `--list` | List all deployments |

**Examples:**

```bash
# Delete deployment and clean up everything
archi delete -n my-archi --rmi --rmv

# Delete but keep data volumes
archi delete -n my-archi --rmi
```

---

### `archi restart`

Restart a specific service in an existing deployment without restarting the entire stack.

```bash
archi restart --name <name> --service <service> [OPTIONS]
```

| Option | Description | Default |
|--------|-------------|---------|
| `--name`, `-n` | Name of the existing deployment | Required |
| `--service`, `-s` | Service to restart | `chatbot` |
| `--config`, `-c` | Updated configuration file(s) | — |
| `--config-dir`, `-cd` | Directory containing configuration files | — |
| `--env-file`, `-e` | Updated secrets file | — |
| `--no-build` | Restart without rebuilding the container image | Off |
| `--with-deps` | Also restart dependent services | Off |
| `--podman`, `-p` | Use Podman instead of Docker | Docker |
| `--verbosity`, `-v` | Logging verbosity (0-4) | `3` |

**Examples:**

```bash
# Quick config update (no rebuild needed)
archi restart -n my-archi --service chatbot --no-build

# Rebuild after code changes
archi restart -n my-archi --service chatbot -c updated_config.yaml

# Re-scrape data sources
archi restart -n my-archi --service data_manager

# Restart with updated secrets
archi restart -n my-archi --service chatbot -e new_secrets.env --no-build
```

---

### `archi list-services`

List all available services and data sources with descriptions.

```bash
archi list-services
```

---

### `archi list-deployments`

List all existing deployments.

```bash
archi list-deployments
```

---

### `archi evaluate`

Launch the benchmarking runtime to evaluate configurations against a set of questions and answers.

```bash
archi evaluate --name <name> --env-file <secrets.env> --config <config.yaml> [OPTIONS]
```

Supports the same flags as `create` (`--podman`, `--gpu-ids`, `--tag`, `--hostmode`, `--verbosity`, `--force`). Configuration files should define the `services.benchmarking` section.

**Example:**

```bash
archi evaluate -n benchmark \
  -c examples/benchmarking/benchmark_configs/example_conf.yaml \
  -e .secrets.env --gpu-ids all
```

See [Benchmarking](benchmarking.md) for full details on query format and evaluation modes.

---

### `archi sources build`

Regenerate a web `sources.list` from a typed YAML **manifest** of seeds, and
optionally trigger a re-ingest. This replaces the manual workflow of downloading
sitemaps, extracting `<loc>` URLs by hand, and concatenating per-site `.list`
files.

```bash
archi sources build <manifest> [OPTIONS]
```

| Option | Description | Default |
|--------|-------------|---------|
| `<manifest>` | Path to the YAML manifest of seed entries (positional, required) | — |
| `--config`, `-c` | Deployment config; resolves the default `--output` and feeds `--import` | — |
| `--output`, `-o` | Target `sources.list` path (overrides the config-derived default) | — |
| `--name`, `-n` | Deployment name to refresh when `--import` is set | — |
| `--services`, `-s` | Services for the `--import` refresh (must be non-empty) | `chatbot` |
| `--env-file`, `-e` | Secrets `.env` forwarded to the `--import` refresh | — |
| `--import` | Re-ingest after writing (requires `--name` and `-c/--config`; not with `--dry-run`) | Off |
| `--dry-run` | Print a unified diff against the existing list and write nothing | Off |

**Output path resolution.** With `--output` the given path is the target. Without
it, the path is read from the config's `data_manager.sources.links.input_lists`,
which is a list: the default is resolved **only when the config declares exactly
one** `input_lists` entry. With zero or several entries the command exits
non-zero and asks you to pass `--output`.

**Behavior.** The list is regenerated wholesale: one URL per line, every URL
normalized (fragment dropped, scheme/host lowercased, a single trailing path
slash collapsed) and deduplicated preserving first-seen order. If a
`manual-extras.list` sits beside the output, its non-comment entries are appended
verbatim — preserving `git-`/`sso-`/`elog-`/`indico-` prefixes — with the
generated block winning position (an extras line duplicating a generated URL
appears once). Any fetch failure (non-200, timeout, malformed XML/HTML) aborts
the whole build and leaves the existing list untouched.

**Examples:**

```bash
# Preview the diff before writing anything
archi sources build sources.manifest.yaml -c config.yaml --dry-run

# Write the resolved single input_lists target
archi sources build sources.manifest.yaml -c config.yaml

# Write to an explicit path
archi sources build sources.manifest.yaml -o ./weblists/sources.list

# Write and re-ingest the `dev` deployment
archi sources build sources.manifest.yaml -c config.yaml \
  --name dev --env-file .secrets.env --import
```

See [Data Sources](data_sources.md#building-a-sourceslist-from-a-manifest) for
the manifest format.

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `ARCHI_DIR` | Override the deployment directory (default: `~/.archi`) |
| `OLLAMA_HOST` | Ollama server address (default: `http://localhost:11434`) |

---

## Troubleshooting

### Port Conflicts

If a port is already in use, the CLI will report an error. Adjust `services.*.external_port` in your config:

```yaml
services:
  chat_app:
    external_port: 7862  # default: 7861
  grafana:
    external_port: 3001  # default: 3000
```

### GPU Issues

GPU access requires NVIDIA drivers and the NVIDIA Container Toolkit.

**Podman:**
```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list
```

**Docker:**
```bash
sudo nvidia-ctk runtime configure --runtime=docker
```

### Verbose Logging

Add `-v 4` to any command for debug-level output:

```bash
archi create [...] -v 4
```

### Multiple Deployments

Multiple deployments can run on the same machine. Container networks are separate, but be careful with external port assignments. See [Advanced Setup](advanced_setup_deploy.md#running-multiple-deployments-on-the-same-machine).
