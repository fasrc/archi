## Why

The `mattermost` service crashes at start-up for three reasons, and each crash hides the next (issue #607, measured on `origin/dev` `6156b760`):

1. `src/bin/service_mattermost.py:main()` never calls `PostgresServiceFactory.set_instance(...)`, so the first `get_full_config()` (in `Mattermost.__init__`, `src/interfaces/mattermost.py:54`, and in `DataManager.__init__`) raises `ConfigNotReadyError`.
2. `MattermostAIWrapper.__init__` (`src/interfaces/mattermost.py:24`) calls `archi()` with no `pipeline`, which raises `TypeError`. `__call__` (`:35`) also passes the history as a positional argument.
3. `Mattermost.__init__` reads `utils.mattermost`, but the rendered config puts the block under `services.mattermost` (`src/cli/templates/base-config.yaml:138`). `mattermost_config` is `None`, so `main()` fails on `None["update_time"]` (`service_mattermost.py:26`).

The service also logs its credentials: `mattermost.py:68-71` pass the webhook URL, both channel IDs, and the access token (`PAK`) to `logger.debug` with no `%s`, so the logging module prints a "Logging error" block that contains them. `:36`, `:175` have the same format bug, and `:213` prints the whole post and answer to stdout.

No unit test imports `src/interfaces/mattermost.py` or `src/bin/service_mattermost.py`, so none of this was found.

## What Changes

- `service_mattermost.main()` sets up `PostgresServiceFactory` (as `src/bin/service_chat.py:25-28` and `src/bin/service_slack.py:13-16` do) before it builds `Mattermost`.
- `MattermostAIWrapper` builds the agent as `PiazzaAIWrapper` does (`src/interfaces/piazza.py:21-50`): `agent_class` (default `"QAPipeline"`), optional agent spec from `agents_dir`, `default_provider`, `default_model`, and `prompts`, all read from `services.mattermost`. It calls the agent with `history=[("User", <message>)]`.
- `Mattermost.__init__` reads `services.mattermost`, not `utils.mattermost`.
- The four credential `logger.debug` lines are deleted. The three format-misuse calls and the `print` become logger calls that put values in the message text, and the `print` no longer writes the post or the answer body.
- One line in `docs/docs/services.md` "Mattermost Interface" lists the optional agent keys.

Out of scope (issue #607 section 2): the hard-coded CERN server URL, bot user ID, state-file path, `town-square` channel, the 30 s start-up sleep, and the same missing factory set-up in `src/bin/service_piazza.py`. No new keys go into `base-config.yaml`; the code defaults match Piazza.

## Capabilities

### New Capabilities
- `mattermost-service`: start-up order, agent construction, config location, and secret-free logging of the Mattermost integration service.

### Modified Capabilities
None.

## Impact

- `src/bin/service_mattermost.py`, `src/interfaces/mattermost.py`, `docs/docs/services.md`.
- New test file `tests/unit/test_mattermost_startup.py`.
- Runtime effect only for a deployment that enables the `mattermost` service; no other service changes.
