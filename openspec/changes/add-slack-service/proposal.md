## Why

archi users can ask questions only in the web chat UI. Many of them work in Slack, and no
archi service answers there: the only Slack code is the Piazza service's outbound webhook
(`src/interfaces/piazza.py:87-89`), which posts drafts and reads nothing. The Mattermost
service is not a usable model. It crashes at startup because it calls `archi()` without the
required `pipeline` argument (`src/interfaces/mattermost.py:24`, `src/archi/archi.py:17`),
and it hard-codes a CERN server, bot user ID, and state path. Issue fasrc/archi#510 records
the operator decision (2026-10-01) to build a Slack service.

## What Changes

- Add a Slack bot module, `src/interfaces/slack_bot.py`. The bot answers `@archi` mentions in
  channels and direct messages, and replies in the Slack thread.
- The bot is a thin HTTP client. It sends each question, with the earlier turns of its Slack
  thread, to the chat app's existing OpenAI-compatible endpoint `POST /v1/chat/completions`.
  It does not load the answer pipeline, the vector store, or any model.
- The bot connects to Slack in Socket Mode: an outbound websocket, so no public inbound URL
  is necessary.
- Add the entry point `src/bin/service_slack.py`.
- Add one dependency, `slack_sdk==3.45.0` (zero required dependencies; its built-in Socket
  Mode client needs no websocket package). Issue #510 named 3.44.1; 3.45.0 is the current
  stable release on 2026-10-01 and has the same dependency shape.
- PR 2 of this change (same change, second PR) wires the service into the CLI: service
  registry entry, Docker Compose block, `Dockerfile-slack`, `base-config.yaml` defaults, render
  tests, and docs. PR 1 delivers the module and its tests only, so no deployment changes yet.

## Capabilities

### New Capabilities
- `slack-integration`: a Slack bot service that answers mentions and direct messages through
  the chat app's `/v1` API, keeps one archi conversation per Slack thread, and never logs a
  token.

### Modified Capabilities
<!-- none: the /v1 endpoint is used as it is; no existing spec changes -->

## Impact

- New code: `src/interfaces/slack_bot.py`, `src/bin/service_slack.py`,
  `tests/unit/test_slack_bot.py`.
- Dependencies: `pyproject.toml` `[project].dependencies` and
  `requirements/requirements-base.txt` gain `slack_sdk==3.45.0`.
- Runtime prerequisites (documented in PR 2): the deployment sets
  `services.chat_app.openai_compat.enabled: true`; a Slack workspace admin creates the Slack
  app and supplies `SLACK_BOT_TOKEN` (`xoxb-…`) and `SLACK_APP_TOKEN` (`xapp-…`); when chat
  authentication is on, an `ARCHI_API_TOKEN` minted by `POST /api/users/me/api-token`.
- No change to `src/interfaces/chat_app/`. The Mattermost crash is out of scope here; it is
  reported separately.
