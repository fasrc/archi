## ADDED Requirements

### Requirement: Self-contained dashboard deployment

The system SHALL provide a Homer dashboard deployment under `config/homer/` consisting of a Docker Compose file (`compose.yaml`), a static Homer configuration (`assets/config.yml`), and operator documentation (`README.md`). The deployment MUST run independently of `config/compose.yaml` and MUST NOT modify any existing service.

#### Scenario: Dashboard starts on its own

- **WHEN** an operator runs `docker compose -f config/homer/compose.yaml up -d`
- **THEN** a Homer container starts using the `b4bz/homer` image with `config/homer/assets/` bind-mounted read-only
- **AND** no other service in the deployment is started, stopped, or restarted

#### Scenario: Dashboard stops cleanly

- **WHEN** an operator runs `docker compose -f config/homer/compose.yaml down`
- **THEN** the Homer container is removed
- **AND** no persistent volume or other service state remains

### Requirement: Dashboard reachable on a non-colliding port

The Homer container SHALL serve the dashboard over HTTP on a port that does not collide with any service in the archi deployment (archi chatbot 7861, vLLM 8000, RAGAS judge 7881, Grafana 3000, LibreChat 3080, Open WebUI 8081). The chosen port is 8082, served via host networking.

#### Scenario: Dashboard answers HTTP

- **WHEN** the Homer container is running
- **AND** a client requests `http://localhost:8082/`
- **THEN** the response is the Homer dashboard HTML

#### Scenario: No port conflict at startup

- **WHEN** the full archi stack (archi chatbot, vLLM, RAGAS, Grafana, LibreChat, Open WebUI) is running
- **AND** the operator starts the Homer deployment
- **THEN** the Homer container binds port 8082 successfully without a conflict

### Requirement: Dashboard lists all operator-facing services

The dashboard configuration SHALL include one tile for each of: the primary archi chatbot, Open WebUI, LibreChat, the vLLM `/v1` endpoint, and Grafana. Each tile MUST have a human-readable name, a one-line description of the service's purpose, and a URL. Tiles MUST be grouped into thematic sections (at minimum: a "Chat & Inference" section and an "Observability" section).

#### Scenario: All five services present

- **WHEN** the dashboard is rendered in a browser
- **THEN** tiles are shown for archi chatbot, Open WebUI, LibreChat, vLLM `/v1`, and Grafana
- **AND** each tile shows a name, a description, and a working link

#### Scenario: Tiles are grouped

- **WHEN** the dashboard is rendered
- **THEN** the chat/inference services appear under a "Chat & Inference" section
- **AND** Grafana appears under an "Observability" section

### Requirement: Service URLs are localhost defaults with retarget guidance

Tile URLs SHALL default to `http://localhost:<port>` using each service's canonical port, and `assets/config.yml` MUST contain comments documenting the canonical port for each tile. `README.md` MUST explain how to replace `localhost` with a fully-qualified domain name for non-host-local deployments.

#### Scenario: Localhost URLs by default

- **WHEN** an operator inspects `config/homer/assets/config.yml`
- **THEN** each tile URL begins with `http://localhost:` and uses the service's documented port
- **AND** a comment next to each tile names the canonical port

#### Scenario: Retargeting documented

- **WHEN** an operator reads `config/homer/README.md`
- **THEN** it describes how to change tile URLs from `localhost` to an FQDN

### Requirement: Config edits apply without rebuilding the image

Because `assets/` is bind-mounted, editing `config/homer/assets/config.yml` and reloading the dashboard in a browser SHALL reflect the change without rebuilding the image. A container restart MAY be required only if the operator changes `compose.yaml` itself.

#### Scenario: Live config edit

- **WHEN** the Homer container is running
- **AND** an operator edits `config/homer/assets/config.yml` (e.g., adds a tile)
- **AND** the operator reloads `http://localhost:8082/` in a browser
- **THEN** the dashboard reflects the edit without any `docker` command being run
