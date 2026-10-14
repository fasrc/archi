## Why

The Slack bot answers every user who can message it. `should_answer()` (`src/interfaces/slack_bot.py:84` on `origin/dev` `c2e885ca`) drops bot messages and events that are not a DM or an @mention, but it has no check on the user or the channel. A guest, or a user from another organization in a Slack Connect channel or DM, can query the knowledge base. Any member can also invite the bot to any channel. On 2026-10-13 the operator asked to limit the bot to internal users (guests count as external), to post a notice to rejected users, and to keep the bot in two channels only. The operator asked for config keys, not hard-coded values.

## What Changes

- New config key `services.slack.internal_users_only` (default `false`). When it is `true`, the bot looks up each sender with `users.info`. It rejects users of another workspace, guests (`is_restricted`, `is_ultra_restricted`), and users that it cannot look up.
- New config key `services.slack.external_user_text` (default: built-in text). A rejected user gets this text as an ephemeral reply.
- New config key `services.slack.allowed_channels` (default `[]`, no limit). The bot answers mentions only in the listed channels. It leaves an unlisted channel at start-up and when someone adds it to one. DMs are not channels, so the list does not apply to them.
- The feedback buttons obey `internal_users_only`.
- With `internal_users_only`, earlier thread messages by rejected users do not go to `/v1` as context.
- The manifest gets `mpim:history`, which group-DM threads need (a gap from before this change).
- The app manifest gets the scopes `users:read`, `channels:read`, `groups:read`, `mpim:read`, `channels:manage`, `groups:write`, `mpim:write` and the event `member_joined_channel`.

## Capabilities

### New Capabilities

- `slack-access-limits`: config keys that limit the Slack bot to internal users and to a list of channels.

### Modified Capabilities

None. (The `slack-integration` spec is not archived yet.)

## Impact

- `src/interfaces/slack_bot.py`, `src/cli/templates/base-config.yaml`, `docs/docs/services.md`, `docs/docs/configuration.md`.
- Tests: `tests/unit/test_slack_access.py` (new), `tests/unit/test_slack_service_wiring.py`.
- An operator who turns a key on must add the new scopes to the Slack app and reinstall it.
