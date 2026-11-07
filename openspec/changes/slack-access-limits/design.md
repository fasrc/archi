## Context

The bot receives Slack events over Socket Mode in `SlackBot.handle_request()`. It acknowledges each event first, then filters, deduplicates, and queues it.

## Decisions

### D1. Internal means a full member of the bot's workspace

`is_internal_user()` reads one `users.info` user. A user is internal when the user is not deactivated (`deleted`), is not a guest (`is_restricted`, `is_ultra_restricted`), is not a stranger (`is_stranger`), and has the `team_id` of the bot's workspace. On Enterprise Grid, a user of another workspace with the same `enterprise_id` is internal. The workspace comes from `auth.test` at start-up. If `auth.test` gives no `team_id`, the event's `team_id` is the fallback.

### D2. Look up each question, and fail closed

The bot calls `users.info` for each question and each feedback click, and keeps no result. A cache would let a member who became a guest keep access until a restart. One lookup is small next to one answer. If `users.info` fails, the bot rejects the message and posts the general error text, not the external notice, because the sender can be internal.

### D3. The notice is ephemeral and is posted once

The bot sends `external_user_text` with `chat.postEphemeral`, so only the sender sees it. It sets `thread_ts` only when the question is already in a thread: Slack does not show an ephemeral reply that would start a new thread. The bot acknowledges each event before it works on it, so Slack does not send it again. As a result, the bot retries a failed post one time itself, as `_deliver_answer` does. Deduplication runs before the check, so a retried envelope does not post the notice twice. The bot sends no question to `/v1` for a rejected user.

### D3a. The check runs in the thread's ordered worker

The Socket Mode client runs listeners in a thread pool. A `users.info` call in the listener could let a later question in a thread reach the queue first. So the listener only filters, deduplicates, and queues. `answer()`, which runs the questions of one thread in order, does the access check first.

### D3b. Thread history obeys the same rule

`answer()` sends the earlier messages of a thread to `/v1` as context. Without a filter, a guest could write in a thread and reach archi when an internal user asks there. With `internal_users_only`, `_authorized_replies()` checks each author of the thread once per question and drops the messages of rejected authors. The asker and the bot are known to be allowed. Integration messages with only a `bot_id` stay, as before.

### D3c. Feedback is deduplicated before the check

A rejected feedback click is checked after `_first_interaction()`, so a retried envelope repeats neither the `users.info` call nor the notice.

### D4. Channel limit: ignore, then leave

A mention in an unlisted channel gets no answer and no notice. The bot also leaves unlisted channels. At start-up, `leave_unlisted_channels()` reads `users.conversations` one type at a time (public, private, and group DMs) and leaves each unlisted channel. Each type needs its own read scope, so a missing scope for one type does not stop the others. While the bot runs, a `member_joined_channel` event for the bot in an unlisted channel causes `conversations.leave`. Slack sends that event only for channels the bot is already in, so it does not report the bot's own join. As a result, a mention in an unlisted channel also makes the bot leave: the mention proves that the bot is a member. DMs stay allowed, because the request was about channels. A failed leave is logged and never stops the bot. Slack refuses to let a bot leave the general channel (`cant_leave_general`). The limit still holds there, because the bot answers no mention in an unlisted channel, so the bot logs the error and stays.

### D5. Config keys, not constants

All three values come from `services.slack`. The template passes `internal_users_only` and `allowed_channels` through as JSON, unconverted, with `false` and `[]` as the defaults for a missing or null key. The bot validates the raw values at start-up: a truthiness check in the template would turn `"false"` into `true` and `""` into `[]`. The template renders `external_user_text` as a JSON string. JSON is valid YAML and escapes quotes safely. The template renders the built-in notice when the key is unset, because `ConfigManager` treats a key that renders `null` by default as a required field. A test keeps the template text equal to `DEFAULT_EXTERNAL_USER_TEXT`. A single string for `allowed_channels` becomes a list of one. Only `null` or `[]` means no limit. A value with no channel IDs (a blank string, or a list of blanks) or of another type stops the bot at start-up with an error that names the key, so a config mistake cannot remove the limit. `internal_users_only` must be a bool or null; any other value stops the bot.
