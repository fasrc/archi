## Context

`archi create` (primary) and `archi evaluate` (benchmarking) both render `src/cli/templates/base-compose.yaml` from the same Jinja2 template, fed by `ServiceBuilder.build_compose_config()` and `TemplateManager._extract_port_config()` in `src/cli/managers/templates_manager.py`. Defaults for each service's `port` and `external_port` are baked into `src/cli/templates/base-config.yaml` (e.g. data_manager `port: 7871`, `external_port: 7871`). User configs override these per-service via the `services.<name>.external_port` key.

`archi evaluate` always runs with `host_mode=False` (`src/cli/cli_main.py:548`). In bridge mode, services are published with `ports: - {external_port}:{container_port}` — docker-proxy binds `external_port` on the host. The primary deployment, when started with `--hostmode`, instead runs services in `network_mode: host` and binds their `port` directly on the host's network namespace.

The collision: both deployments inherit `data_manager.external_port: 7871`. Primary in host mode binds 7871 directly; RAGAS in bridge mode publishes 7871 → 7871. First-to-start wins; the other's listener fails silently inside docker-proxy or the inner process retries forever.

A pre-flight `_check_ports_available` (templates_manager.py:524) probes the port via `socket.bind` before deploying. It correctly catches collisions at deploy time — but only if the *other* deployment is already up when `archi evaluate` runs. If RAGAS came up first (as in the user's environment), the primary deploy is the one that hits the bind error later, and operators tend to skip the check by re-running with `-f` or restarting.

## Goals / Non-Goals

**Goals:**
- A primary `archi create [--hostmode]` deployment and a `archi evaluate -n ragas-*` deployment can coexist on the same host without contesting any host port.
- Zero code changes — keep the diff to a single config file.
- Operator can grep one file (`config/benchmarking/ragas.yaml`) to see exactly which host ports the eval deployment reserves.

**Non-Goals:**
- Auto-allocating ports per deployment name (would require CLI plumbing and a registry; overkill for two known deployments).
- Adding a `port_offset` global or per-deployment `--port-offset` flag.
- Removing the data-manager external publish entirely (it's reachable internally via the compose network, but operators sometimes want to peek at `/api/health` on the host — keep a host port, just not the default).
- Fixing the silent-listener-loser behavior of `network_mode: host` vs bridge collisions. The pre-flight check already covers that path when used; not in scope here.

## Decisions

### 1. Move benchmarking external ports into the 78xx range, offset from defaults

**Choice:** Set `services.data_manager.external_port: 7881` in `config/benchmarking/ragas.yaml`. (78x → 78x+10 mnemonic: "the ragas family of ports.") If chat_app/grader/grafana ever get enabled in a benchmarking deploy, follow the same +10 convention.

**Why:** Memorable, predictable, and visible. An operator running both deployments only needs to remember "RAGAS lives at 78x+10." 7881 is well outside any default in `base-config.yaml` and outside common service ports.

**Why not pick a random high port like 17871:** Random ports are forgettable and harder to firewall-allow uniformly. The +10 convention makes future RAGAS-only services trivially predictable.

**Why not `external_port: 0` (let docker assign):** Docker-assigned ports change per restart, making it impossible for an operator (or a script) to hit data-manager's HTTP endpoints without `docker port` round-trips first.

### 2. Configure only the services the benchmarking deployment actually enables

**Choice:** Override `external_port` only for `data_manager` (and `postgres` if `host_mode` ever gets enabled for evals — but that path doesn't exist today).

**Why:** `archi evaluate` enables exactly `["postgres", "benchmarking"]` (`cli_main.py:521`). Postgres has no `ports:` block in the template (bridge-mode benchmarking doesn't publish it), so no host port collision is possible. Benchmarking is a one-shot job container; no `ports:` block either. Only `data_manager` actually publishes a host port. Adding overrides for services that aren't enabled would be dead config that drifts.

### 3. Keep the change in YAML, not in code

**Choice:** No edits to `base-config.yaml`, `base-compose.yaml`, `templates_manager.py`, or `service_builder.py`.

**Why:** The existing port-resolution code already does the right thing — `_resolve_ports_from_config` reads `external_port` from the user config and threads it into the compose template. Touching the resolver risks breaking the primary deployment's port resolution; touching the template defaults risks shifting 7871 for everyone. A YAML-only change is the smallest possible blast radius.

### 4. Document the reserved range in the YAML itself

**Choice:** Add a top-of-services comment in `config/benchmarking/ragas.yaml` explaining "Benchmarking deploys publish on the 78x+10 host-port range to avoid collision with a primary `archi create` deployment."

**Why:** Future operators copy-pasting this file (e.g., for a second benchmarking config like `ragas-cms.yaml`) need to see the convention without grepping prior PRs.

## Risks / Trade-offs

- **[Risk] An operator firewall-allowing the default range may not realize 7881 also needs to be opened.** → **Mitigation:** Document the reserved port in the YAML comment; mention it in the change's `tasks.md` verification step ("hit `localhost:7881/api/health` from the host").

- **[Risk] If a third deployment shows up later (e.g., a second benchmark config), it'll collide with this one.** → **Mitigation:** Out of scope for this change. If/when that happens, switch to a per-deployment naming convention (`ragas_<name>_offset`) or a CLI flag — both larger changes that aren't justified for two deployments.

- **[Risk] Pre-existing RAGAS deployments at `~/.archi/archi-ragas-*/` still have their old (rendered) `compose.yaml` with port 7871.** → **Mitigation:** The deployment is recreated by `archi evaluate -f`. The verification task includes a fresh redeploy. Operators must `archi evaluate -f` (or remove the deploy dir) for the new ports to take effect — the rendered compose lives in `~/.archi/`, not the repo.

- **[Trade-off] The reserved port (7881) is informally chosen.** No registry tracks "RAGAS owns this range." If 7881 ever conflicts with another tool, this convention has to move. Acceptable — it's a single line of YAML to edit.
