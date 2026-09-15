## Why

The archi deployment now spans several independently-launched UIs and observability surfaces (the primary archi chatbot, Open WebUI, LibreChat, Grafana, and the vLLM/`/v1` endpoint). New developers and operators have no single landing page — they must memorize ports or grep compose files to find each service. A lightweight Homer dashboard gives one bookmark that lists every service with its purpose and link.

## What Changes

- Add a self-contained Homer Docker deployment under `config/homer/` consisting of:
  - `compose.yaml` — runs `b4bz/homer:latest` on host networking at port 8081, with `assets/` bind-mounted read-only.
  - `assets/config.yml` — Homer's only stateful surface; defines the dashboard sections, tiles, and per-tile metadata for archi, Grafana, the chatbot, LibreChat, and Open WebUI.
  - `README.md` — short operator notes (start/stop, where to edit links, how to swap localhost URLs for FQDNs).
- Tiles use `http://localhost:<port>` URLs by default with inline comments listing the canonical port for each backend; non-host deployers replace localhost with their FQDN.
- No changes to the existing `config/compose.yaml` — Homer runs as a peer stack so its lifecycle is independent of Open WebUI / archi.

## Capabilities

### New Capabilities
- `service-dashboard`: A single landing page listing every operator-facing UI in the archi deployment, deployed as an isolated Homer container with a static config bind-mount.

### Modified Capabilities
<!-- None: no existing capability has its requirements changed. -->

## Impact

- **New files**: `config/homer/compose.yaml`, `config/homer/assets/config.yml`, `config/homer/README.md`.
- **No code changes**: pure Docker + static YAML; no Python or JS touched.
- **No port collisions** with the existing stack: archi chatbot 7861, vLLM 8000, RAGAS 7881, Grafana 3000, LibreChat 3080, Open WebUI 8081 — Homer takes 8082 to stay clear of Open WebUI on 8081.
- **Operator workflow**: `docker compose -f config/homer/compose.yaml up -d` to start, edit `assets/config.yml` and refresh the browser to update tiles (no container restart needed).
- **External dependency**: pins the public `b4bz/homer` image; no build step.
