## ADDED Requirements

### Requirement: Two coexisting deploys with disjoint container sets

The host SHALL run exactly two archi deployments side by side: a production deployment named `archi-openai-compat` and a development deployment named `archi-openai-compat-dev`. Each deployment SHALL produce a container set whose names are suffixed with the deployment name (e.g., `chatbot-archi-openai-compat`, `chatbot-archi-openai-compat-dev`), and the container sets MUST NOT overlap. No service SHALL be shared between the two deployments at the container, volume, or named-network level.

#### Scenario: Both container sets present

- **WHEN** the operator runs `docker ps --format '{{.Names}}'` after the cutover is complete
- **THEN** the output contains at least `chatbot-archi-openai-compat`, `data-manager-archi-openai-compat`, `postgres-archi-openai-compat`, `grafana-archi-openai-compat`, `chatbot-archi-openai-compat-dev`, `data-manager-archi-openai-compat-dev`, `postgres-archi-openai-compat-dev`, and `grafana-archi-openai-compat-dev`
- **AND** no container name appears in both deployments' compose files

#### Scenario: No shared docker volume

- **WHEN** the operator inspects volumes for either deployment
- **THEN** every volume referenced by `compose.yaml` in `~/.archi/archi-archi-openai-compat/` is distinct from every volume referenced by `compose.yaml` in `~/.archi/archi-archi-openai-compat-dev/`

### Requirement: Production deployment runs without the dev source bind mount

The production deployment SHALL be created without the archi CLI's `--dev` flag. The rendered `compose.yaml` for production MUST NOT contain any bind-mount entry pointing host-side `src/` paths into application containers. Production application code SHALL come exclusively from the container image, built at deploy time from a pinned git ref.

#### Scenario: No src bind mount on prod chatbot

- **WHEN** the operator runs `docker inspect chatbot-archi-openai-compat --format '{{range .Mounts}}{{.Source}}:{{.Destination}}{{println}}{{end}}'`
- **THEN** the output does not contain any line whose source path is `/home/a2rchi/archi-openai-compat/src`
- **AND** the output does not contain any line whose destination is `/root/archi/src`

#### Scenario: Edits to the host prod tree do not affect users

- **WHEN** the operator modifies any file under `/home/a2rchi/archi-openai-compat/src/` and restarts `chatbot-archi-openai-compat` *without* running `archi deploy`
- **THEN** the running container's behavior is identical to before the edit
- **AND** the modified file is not visible inside the container at `/root/archi/src/`

### Requirement: Production deployment is pinned to an immutable git ref

The git checkout at `/home/a2rchi/archi-openai-compat` SHALL sit on a git tag or detached commit, not a branch tip. The intended workflow for production code changes is: create a new tag on `dev`, fetch it in the prod checkout, switch to it, and run `archi deploy` to rebuild the image. A `git pull` MUST NOT change what runs in production.

#### Scenario: Prod tree is on a tag

- **WHEN** the operator runs `git -C /home/a2rchi/archi-openai-compat status` after the cutover
- **THEN** the output describes a detached HEAD on a named tag, or HEAD points at a tag-pinned commit

### Requirement: Development deployment runs with the dev source bind mount

The development deployment SHALL be created with `--dev`. Its `compose.yaml` SHALL contain a bind mount of `/home/a2rchi/archi-openai-compat-dev/src` into the chatbot container at `/root/archi/src`, so that edits in the dev tree take effect on the next container restart without rebuilding any image.

#### Scenario: Src bind mount on dev chatbot

- **WHEN** the operator runs `docker inspect chatbot-archi-openai-compat-dev --format '{{range .Mounts}}{{.Source}}:{{.Destination}}{{println}}{{end}}'`
- **THEN** the output contains a line with source `/home/a2rchi/archi-openai-compat-dev/src` and destination `/root/archi/src`

#### Scenario: Dev edits take effect on restart

- **WHEN** the operator edits a Python file under `/home/a2rchi/archi-openai-compat-dev/src/` and runs `docker restart chatbot-archi-openai-compat-dev`
- **THEN** the next request handled by the dev chatbot reflects the edited code

### Requirement: Development tree tracks the dev branch

The git checkout at `/home/a2rchi/archi-openai-compat-dev` SHALL be configured so that its default branch is `dev` (the upstream branch on origin/fasrc). The operator updates this checkout manually via `git pull` followed by `archi deploy --dev`; no automated polling, cron job, or webhook is part of this capability.

#### Scenario: Dev tree's HEAD branch is dev

- **WHEN** the operator runs `git -C /home/a2rchi/archi-openai-compat-dev status` after a fresh setup
- **THEN** the output reports the current branch as `dev`

### Requirement: vLLM is the only shared backend

Both deployments SHALL be configured with the same OpenAI-compatible provider `base_url` of `http://localhost:8000/v1`, pointing at the host-local vLLM server. No other service (postgres, grafana, data-manager, vectorstore volume, accounts directory) MAY be shared.

#### Scenario: Both configs point at the same vLLM URL

- **WHEN** the operator inspects each deployment's effective `configs/config.yaml`
- **THEN** the OpenAI provider entry in both files has `base_url: http://localhost:8000/v1`

#### Scenario: Postgres is per-deploy

- **WHEN** the operator queries the production postgres
- **THEN** writes against the production database are not visible from any client connected to `postgres-archi-openai-compat-dev`

### Requirement: Disjoint host port allocation

The deployments SHALL bind to disjoint host ports. Production uses chatbot 7861, data-manager 7889, grafana 3000. Development uses chatbot 7891, data-manager 7919, grafana 3030. No `compose.yaml` SHALL declare a host port that any other archi deployment on this host also declares.

#### Scenario: No port collision at compose level

- **WHEN** the operator runs `grep -E '^\s*-\s+[0-9]+:[0-9]+' ~/.archi/archi-archi-openai-compat/compose.yaml ~/.archi/archi-archi-openai-compat-dev/compose.yaml`
- **THEN** every host port number appears in exactly one file

#### Scenario: Both deploys start successfully

- **WHEN** both deployments are started
- **THEN** every container in both sets reaches `Up` status, with none failing on `EADDRINUSE`

### Requirement: Disjoint secrets directories with distinct postgres passwords

Each deployment SHALL have its own directory under `~/.archi/` containing its own `secrets/` subtree. Secret values MAY be copied between deployments (so they start with the same OpenAI/Anthropic/HF keys), but the postgres password MUST differ between production and development. Symlinks between the two secrets directories are not permitted.

#### Scenario: Distinct postgres passwords

- **WHEN** the operator compares `~/.archi/archi-archi-openai-compat/secrets/pg_password.txt` and `~/.archi/archi-archi-openai-compat-dev/secrets/pg_password.txt`
- **THEN** the file contents differ

#### Scenario: Secrets directories are not symlinks

- **WHEN** the operator runs `find ~/.archi/archi-archi-openai-compat-dev/secrets -maxdepth 2 -type l`
- **THEN** the output is empty

### Requirement: Staff reach the dev chatbot via an external port

The dev chatbot's external port (7891) SHALL be reachable from the staff source range via an explicit iptables INPUT rule inserted at position 12, in line with this host's existing firewall convention. Production's external port SHALL continue to be reachable as before. No new external port for dev's data-manager or grafana is required by this capability.

#### Scenario: Iptables rule for dev chatbot port

- **WHEN** the operator runs `sudo iptables -L INPUT -n --line-numbers`
- **THEN** position 12 (or an earlier ACCEPT position) contains a rule allowing tcp/7891 from the staff source range

#### Scenario: Prod port still reachable

- **WHEN** a staff machine in the allowed source range connects to `archi.rc.fas.harvard.edu:7861`
- **THEN** the connection succeeds and serves the production chatbot UI

#### Scenario: Dev port reachable from staff range

- **WHEN** a staff machine in the allowed source range connects to `archi.rc.fas.harvard.edu:7891`
- **THEN** the connection succeeds and serves the dev chatbot UI

### Requirement: Dev deployment is visibly marked as non-production

The dev chatbot UI SHALL display a visible marker (banner, badge, or other prominent affordance) identifying the deployment as development and warning that it may be unstable. The exact rendering is a UI concern; the requirement is that an end user cannot mistake the dev deployment for production at a glance.

#### Scenario: Dev UI shows a non-production marker

- **WHEN** a user loads the dev chatbot UI at `archi.rc.fas.harvard.edu:7891`
- **THEN** the page contains a visible element identifying the deployment as "dev" or "development" before the user submits any input
