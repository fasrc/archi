## Context

The bot receives Slack events over Socket Mode in `SlackBot.handle_request()`. It acknowledges each event first, then filters, deduplicates, and queues it.

## Decisions

### D1. Internal means a full member of the bot's workspace

`is_internal_user()` reads one `users.info` user. A user is internal when the user is not a guest (`is_restricted`, `is_ultra_restricted`), is not a stranger (`is_stranger`), and has the `team_id` of the bot's workspace. On Enterprise Grid, a user of another workspace with the same `enterprise_id` is internal. The workspace comes from `auth.test` at start-up. If `auth.test` gives no `team_id`, the event's `team_id` is the fallback.

### D2. Fail closed, but do not remember a failure

If `users.info` fails, the bot rejects the message. The bot keeps only successful lookups, so the next message from that user tries again. The cache has no expiry: a guest that becomes a full member is accepted after the service restarts.

### D3. The notice is ephemeral and is posted once

The bot sends `external_user_text` with `chat.postEphemeral` in the thread of the question, so only the sender sees it. The check runs after deduplication, so a retried envelope does not post the notice twice. The bot sends no question to `/v1` for a rejected user.

### D4. Channel limit: ignore, then leave

A mention in an unlisted channel gets no answer and no notice. The bot also leaves unlisted channels. At start-up, `leave_unlisted_channels()` reads `users.conversations` (public, private, and group DMs) and leaves each unlisted channel. While the bot runs, a `member_joined_channel` event for the bot in an unlisted channel causes `conversations.leave`. DMs stay allowed, because the request was about channels. A failed leave is logged and never stops the bot.

### D5. Config keys, not constants

All three values come from `services.slack`. The template renders `internal_users_only` as a bool, `allowed_channels` as a JSON list, and `external_user_text` as a JSON string. JSON is valid YAML and escapes quotes safely. The template renders the built-in notice when the key is unset, because `ConfigManager` treats a key that renders `null` by default as a required field. A test keeps the template text equal to `DEFAULT_EXTERNAL_USER_TEXT`. A single string for `allowed_channels` becomes a list of one. Any other type stops the bot at start-up with an error that names the key.
