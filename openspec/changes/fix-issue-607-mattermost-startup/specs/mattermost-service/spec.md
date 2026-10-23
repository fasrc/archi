## ADDED Requirements

### Requirement: The Mattermost service SHALL set up the Postgres factory before it reads config

`src/bin/service_mattermost.py:main()` MUST call `PostgresServiceFactory.from_env(password_override=read_secret("PG_PASSWORD"))` and then `PostgresServiceFactory.set_instance(...)` with the result, before it builds `mattermost.Mattermost()`.

#### Scenario: Start-up order

- **WHEN** `service_mattermost.main()` runs with `PostgresServiceFactory`, `read_secret`, `time.sleep`, and `mattermost.Mattermost` mocked
- **THEN** `from_env` is called with `password_override` equal to the `PG_PASSWORD` secret
- **AND** `set_instance` is called with the `from_env` result
- **AND** both calls occur before `Mattermost()` is built

### Requirement: The Mattermost AI wrapper SHALL build its agent from services.mattermost

`MattermostAIWrapper()` MUST call `archi` with `pipeline=` set to `services.mattermost.agent_class` (default `"QAPipeline"`), and with `default_provider`, `default_model`, and `prompt_overrides` read from `services.mattermost`. Calling the wrapper with a post MUST call the agent with `history=[("User", <post message>)]` and return the agent's `answer`.

#### Scenario: Default agent class

- **WHEN** `services.mattermost` holds only `update_time`
- **THEN** `archi` is called with `pipeline="QAPipeline"`

#### Scenario: Configured agent class

- **WHEN** `services.mattermost.agent_class` is `"CMSCompOpsAgent"`
- **THEN** `archi` is called with `pipeline="CMSCompOpsAgent"`

#### Scenario: Call with history

- **WHEN** the wrapper is called with `{"message": "hello"}`
- **THEN** the agent is called with `history=[("User", "hello")]`
- **AND** the wrapper returns the agent's `answer` and `"hello"`

### Requirement: The Mattermost service SHALL read its config from services.mattermost

`Mattermost().mattermost_config` MUST be the `services.mattermost` block of the full config, and `main()` MUST read `update_time` from it.

#### Scenario: Config block location

- **WHEN** the full config has `services.mattermost.update_time` equal to `60` and no `utils` key
- **THEN** `Mattermost().mattermost_config["update_time"]` is `60`

### Requirement: The Mattermost service SHALL NOT write secrets or post bodies to its output

Building `Mattermost()` MUST NOT write the webhook URL, the channel IDs, or the access token to the log at any level. `process_posts()` MUST NOT write the post or the answer to stdout.

#### Scenario: Secrets absent from a debug log

- **WHEN** `Mattermost()` is built with the secrets `webhook-secret`, `chan-read-secret`, `chan-write-secret`, and `pak-secret`, and the log level is DEBUG
- **THEN** none of the four strings appears in the captured log output

#### Scenario: No stdout on answer

- **WHEN** `process_posts()` answers a new post
- **THEN** nothing is written to stdout
