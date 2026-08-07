## Context

The archi deployment is a multi-process stack: the primary archi chatbot (Flask, port 7861), vLLM (`/v1` OpenAI-compatible server, port 8000), Open WebUI (port 8081 via host networking), LibreChat (port 3080), Grafana (port 3000), and the RAGAS judge service (port 7881). Each is launched independently and there is currently no consolidated entry point. Operators rely on tribal knowledge or a hand-maintained note to find a given UI. The goal is to ship a tiny dashboard that ships a curated link list — not a service mesh, not an SSO portal, not an observability tool.

[Homer](https://github.com/bastienwirtz/homer) is a single-binary static dashboard (nginx serving HTML + a YAML config). It has no backend, no database, and no auth — perfect for a self-hosted launchpad on a trusted host network.

## Goals / Non-Goals

**Goals:**
- One bookmark per host that lists every operator-facing service in archi with a one-line description and a clickable link.
- Self-contained under `config/homer/`, deployable with a single `docker compose up -d` independent of the rest of the stack.
- Trivial to re-target for non-localhost deployments (FQDN swap is a search/replace in `assets/config.yml`).
- No image build, no custom Dockerfile, no entrypoint shim — pin the public `b4bz/homer:latest` image and bind-mount the config.

**Non-Goals:**
- Auth, RBAC, or per-user views (Homer does not support them; out of scope).
- Health-check polling / live status badges (Homer's `ping` feature requires CORS on every backend, which most of these services do not allow on `localhost`; deferred).
- Modifying or coupling to `config/compose.yaml` or any existing service.
- Multi-host or service-discovery integrations (Consul, Traefik labels, etc.).

## Decisions

### D1: Standalone compose at `config/homer/compose.yaml`, not part of the main stack
Homer's lifecycle is fully orthogonal to archi's — losing the dashboard does not affect users, and breaking archi should not also break the dashboard. A separate compose file makes that explicit. The user confirmed this preference. **Alternative considered**: adding a `homer` service to `config/compose.yaml`. Rejected because it couples Homer's restart semantics to Open WebUI's and means a `docker compose down` on the main stack also kills the operator's index page.

### D2: Host networking, port 8082
The dashboard's tiles point at `http://localhost:<port>` URLs that only resolve correctly if Homer shares the host's network namespace (matches what Open WebUI in `config/compose.yaml` already does). Port 8082 is chosen to avoid every known collision in the deployment:

| Port | Service                       |
|------|-------------------------------|
| 3000 | Grafana                       |
| 3080 | LibreChat                     |
| 7861 | archi chatbot (primary)       |
| 7881 | RAGAS judge (port-isolated)   |
| 8000 | vLLM `/v1`                    |
| 8081 | Open WebUI                    |
| **8082** | **Homer (this change)**   |

**Alternative considered**: bridge networking with port mapping. Rejected — Homer is just static files; host networking adds zero risk and one less config knob.

### D3: Hardcoded `localhost` URLs with inline comments, not env-templated
Homer's `assets/config.yml` is loaded by JS in the browser; env substitution would require either a custom entrypoint that templates the file at container start or a sidecar — both pull weight that this change does not need. The user confirmed they want hardcoded localhost URLs. The config will include a comment block at the top that explains how to swap in an FQDN. **Alternative considered**: `envsubst` entrypoint. Rejected as YAGNI for a single-host deployment; we can add it later under a separate change if multi-host comes up.

### D4: Bind-mount `assets/` read-only
Homer reads `/www/assets/config.yml` and (optionally) icon files from the same directory. Mounting `./assets:/www/assets:ro` keeps the container image immutable and gives operators a normal text file to edit. Browser refresh picks up changes without restarting the container.

### D5: Group tiles into thematic sections
Five services split naturally into two groups in Homer's `services` list:

- **Chat & Inference** — archi chatbot, Open WebUI, LibreChat, vLLM `/v1`
- **Observability** — Grafana

Homer renders sections as collapsible columns. Even with one tile in Observability today, the section is cheap and lets future additions (Prometheus, Loki, RAGAS dashboard URL) land without re-organizing.

### D6: Use the `b4bz/homer:latest` image, not pinned to a digest
This is a low-stakes, public image consumed only as a static-file server; tracking `latest` keeps icon/feature updates flowing without manual bumps. **Trade-off**: a regression in upstream could surface on the next `docker compose pull`. Mitigation: the README documents `docker tag` rollback. If the project later standardizes on digest-pinned images we can revisit under a global policy.

## Risks / Trade-offs

- **[Risk] Stale links**: When a backend port changes (e.g., the recent RAGAS shift to 7881), `assets/config.yml` won't auto-update. → **Mitigation**: README calls this out; the file is short enough (one tile per service) that a search/replace on the port number is the entire fix.
- **[Risk] No auth on the dashboard itself**: Anyone who can reach port 8082 sees the link list (and the labels and descriptions). → **Mitigation**: this is a host-network deployment behind the existing host firewall; the dashboard exposes no secrets, only URLs that any operator already knows. Document that users who need a public deployment should put Homer behind their existing reverse-proxy auth (e.g., the same one fronting Grafana).
- **[Risk] Health badges absent**: Operators looking at the dashboard cannot tell at a glance whether vLLM is up. → **Mitigation**: explicitly out of scope (see Non-Goals); operators continue to use existing per-service health checks. A follow-up change can add `ping` once we decide CORS policy across services.
- **[Trade-off] `latest` tag drift**: see D6.

## Migration Plan

This is a pure additive change — no migration required.

- **Deploy**: `docker compose -f config/homer/compose.yaml up -d`
- **Verify**: `curl -s http://localhost:8082/ | grep -i homer` returns matches; browser shows the dashboard.
- **Rollback**: `docker compose -f config/homer/compose.yaml down` removes the container; deleting `config/homer/` removes the config. No persistent state, no other service depends on it.

## Open Questions

- Should the dashboard live on a stable port across all archi deployments (current proposal: 8082), or should it be deployer-configurable? Defaulting to 8082 with a README note seems sufficient unless we hit a host that already uses 8082.
- Future: when (not if) we add a SSO/reverse-proxy layer, do we keep Homer's anonymous-access posture or move it behind the proxy? Defer to whoever proposes the proxy change.
