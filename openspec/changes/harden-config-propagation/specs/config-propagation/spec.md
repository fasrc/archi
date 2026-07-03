## ADDED Requirements

### Requirement: Redeploy re-seeds config and restarts the chat process
A dev redeploy SHALL guarantee that, after configuration is rendered, the config is re-seeded into Postgres `static_config` AND the chat service process is recreated so it re-reads the new config — regardless of the ambient `ARCHI_COMPOSE_UP_FLAGS` value and regardless of whether the operator also issued a bare container restart. The re-seed-then-restart SHALL be sequenced (seed completes before the chat process starts) via the existing `config-seed → chatbot` dependency. Postgres and ingested-corpus data volumes SHALL be preserved and their containers SHALL NOT be force-recreated by this step.

#### Scenario: Redeploy after a provider config change
- **WHEN** an operator changes `services.chat_app.providers.local.extra_kwargs` in `config.yaml` and runs `deploy/fasrc-dev/scripts/redeploy.sh`
- **THEN** the `config-seed` one-shot container SHALL re-run and UPSERT the new value into Postgres `static_config`
- **AND** the `chatbot` container SHALL be recreated (new `StartedAt`) and read the re-seeded config at boot
- **AND** the `postgres` and `data-manager` containers SHALL NOT be recreated and their data volumes SHALL remain intact

#### Scenario: Ambient compose-flags override cannot strip the restart
- **WHEN** the deploy runs in an environment that exports `ARCHI_COMPOSE_UP_FLAGS` without `--force-recreate`
- **THEN** the redeploy SHALL still recreate the `config-seed` and `chatbot` services (via the explicit targeted `up -d --force-recreate config-seed chatbot`)
- **AND** the running chat process SHALL end up serving the freshly seeded config

#### Scenario: Bare container restart does not silently serve stale config
- **WHEN** an operator restarts only the chat container without re-running the config seed after a config change
- **THEN** the documented deploy procedure SHALL direct the operator to the redeploy path that re-seeds first, so the process cannot come up reading a stale `static_config` row

### Requirement: Effective loaded config is observable from logs
The chat service SHALL log its effective resolved provider configuration once at startup, so the configuration the live process actually loaded can be confirmed with a single log query rather than reconstructed by hand. The log SHALL include the resolved `default_provider`, that provider's effective `extra_kwargs`, the `config_version`, and a stable hash of the providers block. Secret-bearing fields SHALL NOT be logged verbatim beyond what is already non-sensitive.

#### Scenario: Confirm the loaded thinking toggle from logs
- **WHEN** the chat container has started and an operator runs `docker logs <chat-container> | grep enable_thinking`
- **THEN** the output SHALL show the effective `chat_template_kwargs.enable_thinking` value the process loaded, together with the `config_version` and providers-block hash

#### Scenario: Detect drift between stored and loaded config
- **WHEN** `static_config` in Postgres has been re-seeded but the process was not restarted
- **THEN** the boot-logged providers hash SHALL differ from a hash computed over the current `static_config`, making the drift detectable

### Requirement: Post-deploy verification asserts the feature toggle against a live turn
Post-deploy verification SHALL drive a live chat turn and assert both a successful response and the feature toggle under test, so a stale or misapplied toggle fails verification immediately instead of reaching users. When a reasoning-suppression toggle is under test, verification SHALL assert the response contains no `</think>` markers or bare chain-of-thought leakage.

#### Scenario: Deploy with enable_thinking disabled
- **WHEN** post-deploy verification runs against a deployment configured with `enable_thinking: false`
- **THEN** verification SHALL send a chat turn and receive HTTP 200 with a non-empty answer
- **AND** verification SHALL assert the answer body contains no `</think>` substring and no leaked reasoning narration

#### Scenario: Verification fails on leaked thinking
- **WHEN** the deployed process is serving thinking-enabled output despite the configured toggle
- **THEN** the `</think>`-leakage assertion SHALL fail and mark the deploy as not verified
