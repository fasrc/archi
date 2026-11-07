## Context

The Mattermost service is an old integration that no deployment enables today, and no unit test imports it. The Slack bot (PR #598) and the Piazza service already have the working pattern: set up `PostgresServiceFactory` first, then read config from Postgres, then build an `archi` agent with an explicit `pipeline`.

## Goals / Non-Goals

**Goals:** the service gets past start-up and can answer one post; it never writes a secret to the log or the post body to stdout.

**Non-Goals:** making the hard-coded CERN values configurable, removing the 30 s sleep, and the Piazza service's missing factory set-up (issue #607 section 2, "Out of scope").

## Decisions

1. **Factory set-up order.** Copy `service_chat.py:25-28`: `PostgresServiceFactory.from_env(password_override=read_secret("PG_PASSWORD"))`, then `PostgresServiceFactory.set_instance(factory)`, before `mattermost.Mattermost()`. Import `PostgresServiceFactory` at module level in `service_mattermost.py` so a test can monkeypatch it there (the pattern of `tests/unit/test_slack_bot.py::test_service_slack_main_sets_up_config_then_runs`).
2. **Agent construction mirrors Piazza, not a shared helper.** `PiazzaAIWrapper.__init__` is 25 lines. A shared helper would touch Piazza, which is out of scope and has no tests. Copy the logic with `services.mattermost` in place of `services.piazza`, including the `agents_dir` fallback to `services.chat_app.agents_dir` and the `AgentSpecError` warning.
3. **Config read.** `get_full_config().get("services", {}).get("mattermost", None)`. The template always renders `services.mattermost.update_time`, so `main()` keeps `int(mattermost_config["update_time"])`. The wrapper reads its keys with `.get(...)` on `services.mattermost or {}` so missing optional keys use the Piazza defaults.
4. **Logging.** Delete `:68-71`. Replace `logger.debug("ANSWER = ", answer)` and `logger.info("Loaded data:", data)` with f-string messages that hold no secret (`data` holds only answered post IDs). Replace the `print` at `:213` with `logger.info(f"Mattermost: answered post {topic['id']}")`. `setup_logging` prints only `%(message)s`, so `extra=` is not used.
5. **No template change.** Adding `services.mattermost.agent_class` to `base-config.yaml` would also need a `_NON_BOOL_DEFAULT_BASELINE` entry; the code default is enough, and the docs line names the optional keys.

## Risks / Trade-offs

- Tests must mock `DataManager`, `archi`, `get_full_config`, `read_secret`, and `requests` in the `src.interfaces.mattermost` namespace; a test that misses one can touch Postgres. Each test monkeypatches all of them.
- The Piazza copy can drift from the Mattermost copy. Accepted: both are small and the issue scopes this to Mattermost.
