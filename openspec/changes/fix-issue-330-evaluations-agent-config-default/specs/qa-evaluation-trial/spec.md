## ADDED Requirements

### Requirement: An enabled evaluations console is refused at create time, never rendered dead

`archi create` SHALL refuse a deployment configuration that sets `services.chat_app.evaluations.enabled` to exactly `true` without an `agent_config_path` the console seam can accept, and the rendered running configuration SHALL NOT supply a default value for that key. The refusal MUST name `services.chat_app.evaluations.agent_config_path` and MUST occur before any destructive step, so a `--force` re-create that was always going to produce a dead console leaves the existing deployment intact.

Two values are unacceptable: an absent or blank path, and a path that names the live
deployment configuration `/root/archi/configs/config.yaml`. Both are already refused at
runtime by `build_evaluation_service`, which logs one error and returns `None`. That refusal
is correct fork policy and stays: every run copies the named file into a host-mounted run
workspace the console then serves, so the live config must never be the snapshot source.

The defect this requirement closes is that the template rendered the refused value as the
default, so an operator who set only `enabled: true` got a deployment that started clean,
exited 0, and had no console. The only evidence was one line in a container log. No rendered
default can ever work, because the only path a template can know is the live one.

The create-time check and the runtime check ask deliberately different questions about the
same path. The CLI runs on the host, where the live config normally does not exist, so it
compares normalized paths only. The seam runs in the container, where both files exist, so it
keeps its device-and-inode identity check and catches a hard link or bind mount as well. The
create-time check is a strict subset and a fast diagnostic; the seam remains the authority,
because a running configuration can be changed after create.

#### Scenario: An enabled console with no path refuses create and names the key

- **WHEN** `archi create` runs with the `chatbot` service enabled and a configuration that
  sets `services.chat_app.evaluations.enabled: true` and no `agent_config_path`
- **THEN** the command exits non-zero
- **AND** the error names `services.chat_app.evaluations.agent_config_path`
- **AND** no deployment files are rendered

A blank or whitespace-only value is the same case. An operator who typed a key and left it
empty gets the same message as one who omitted it.

#### Scenario: An enabled console naming the live deployment config refuses create

- **WHEN** the configuration sets `evaluations.enabled: true` with
  `agent_config_path: /root/archi/configs/config.yaml`, including any spelling that
  normalizes to that path
- **THEN** `archi create` exits non-zero
- **AND** the error names the key and states that the live deployment config is refused

This is the case an operator reaches by copying the value out of `docs/docs/configuration.md`.
Without it, mechanism (b) would refuse the omitted key and still pass the documented one.

#### Scenario: The refusal precedes the forced teardown

- **WHEN** `archi create --force` runs against an existing deployment with a configuration
  whose evaluations block is refused by either rule above
- **THEN** the command exits non-zero
- **AND** `delete_deployment()` is never called
- **AND** the existing deployment directory and its contents are left intact

The check therefore lives in `config_manager.validate_configs()`, which runs above the
teardown, and not in the template staging seam, which runs below it. A refusal below the
teardown is the defect that `cli-create-preflight` exists to prevent.

#### Scenario: A disabled console renders no path and never refuses

- **WHEN** the configuration omits the `evaluations` block, omits `enabled`, or sets
  `enabled` to anything other than boolean `true`
- **THEN** `archi create` does not refuse on evaluations grounds
- **AND** the rendered running configuration carries `agent_config_path` as `null`, not the
  live deployment config path

`null` is inert here: the seam returns before reading the key when the console is off. The
rendered value must not be the string `"null"` either, which a filename check would accept.

#### Scenario: An accepted path renders through to the running configuration

- **WHEN** the configuration sets `enabled: true` with an `agent_config_path` that is a
  non-empty string and is not the live deployment config
- **THEN** `archi create` does not refuse on evaluations grounds
- **AND** the rendered running configuration carries that exact path

#### Scenario: One definition of the live-config path serves both sides

- **WHEN** the live deployment config path is needed by the CLI validator and by the chat
  app seam
- **THEN** both read it from one shared module that imports no web framework, so the two
  sides cannot drift

The whole defect was two places disagreeing about one path. A second literal would leave
that possible.
