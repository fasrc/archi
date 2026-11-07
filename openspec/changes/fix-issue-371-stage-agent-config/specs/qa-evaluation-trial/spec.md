## ADDED Requirements

### Requirement: Evaluation agent config staging at deploy time
`archi create` SHALL treat an enabled console's `services.chat_app.evaluations.agent_config_path` as a host source path (absolute, or relative to the deployment YAML), SHALL refuse a missing, non-file, unreadable, live-config, or inside-the-deployment-directory source before `remove_existing_deployment()` runs, SHALL copy the file to `evaluation_config/qa_agent_config.yaml`, and SHALL rewrite the running config's `agent_config_path` to `/root/archi/evaluation_config/qa_agent_config.yaml`.
The old workaround put a redacted copy in `<base_dir>/configs/`, which `archi create --force` deletes with the rest of the deployment directory, so every redeploy left an enabled console whose runs all failed the file check. Archi copies the file on every create; the operator still supplies and redacts it.

#### Scenario: Agent config staged and mounted
- **WHEN** the deploy config enables the console and sets `agent_config_path` to an existing host file, and `archi create` renders the deployment
- **THEN** the file is copied to `evaluation_config/qa_agent_config.yaml`
- **AND** the compose file mounts `./evaluation_config` read-only at `/root/archi/evaluation_config`
- **AND** the rendered running config's `agent_config_path` is `/root/archi/evaluation_config/qa_agent_config.yaml`

#### Scenario: Missing host file refused before teardown
- **WHEN** `archi create --force` runs against an existing deployment and the enabled console's `agent_config_path` names a host file that does not exist
- **THEN** create exits non-zero with a message that names `services.chat_app.evaluations.agent_config_path` and the resolved host path
- **AND** the existing deployment directory is still present

#### Scenario: Live deployment config still refused
- **WHEN** `agent_config_path` resolves to the operator's own deployment YAML, or spells `/root/archi/configs/config.yaml`
- **THEN** create refuses it with a message that names the key and says the live deployment config is refused

#### Scenario: Source inside the deployment directory refused
- **WHEN** `agent_config_path` resolves to a file under the deployment directory (for example `<base_dir>/configs/config.eval.yaml`)
- **THEN** create refuses it before the teardown with a message that names the source path and says to move it outside the deployment directory

#### Scenario: Disabled console stages nothing
- **WHEN** `evaluations.enabled` is not `true`
- **THEN** no host file is read, no `qa_agent_config.yaml` is staged, and any stale staged copy is removed

## MODIFIED Requirements

### Requirement: Evaluation MCP registry staging at deploy time

`archi create` SHALL treat a configured
`services.chat_app.evaluations.mcp_config_path` as a host source path: validate it,
stage it into the generated `evaluation_config/` directory, mount it read-only into
the chatbot container, and rewrite the running config's `mcp_config_path` to the
fixed container path.

#### Scenario: Registry staged and mounted

- **WHEN** the deploy config sets `mcp_config_path` to a valid registry file and
  `archi create` renders the deployment
- **THEN** the file is copied to `evaluation_config/qa_evaluation_mcp.yaml`, the
  compose file mounts that directory read-only, and the rendered running config
  points at `/root/archi/evaluation_config/qa_evaluation_mcp.yaml`

#### Scenario: No registry, no mount

- **WHEN** the deploy config leaves `mcp_config_path` unset and no agent config is
  staged
- **THEN** no `qa_evaluation_mcp.yaml` staging occurs and the compose file has no
  evaluation-config mount

#### Scenario: No registry, agent config staged

- **WHEN** the deploy config leaves `mcp_config_path` unset but an enabled console's
  agent config is staged
- **THEN** the compose file still mounts `./evaluation_config` read-only, and the
  rendered running config's `mcp_config_path` is `null`
