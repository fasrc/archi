## ADDED Requirements

### Requirement: The Slack bot SHALL answer only internal users when internal_users_only is true
When `services.slack.internal_users_only` is `true`, the bot MUST look up the sender of each question with `users.info`. It MUST NOT send the question to `/v1` if the user is a guest, belongs to another workspace (other than one in the same enterprise), or cannot be looked up. When the key is `false` or absent, the bot MUST NOT call `users.info`.

#### Scenario: An internal user
- **WHEN** a full member of the bot's workspace mentions the bot
- **THEN** the bot answers the question

#### Scenario: A guest
- **WHEN** a user with `is_restricted` or `is_ultra_restricted` mentions the bot
- **THEN** the bot sends nothing to `/v1`

#### Scenario: A Slack Connect user
- **WHEN** a user whose `team_id` is another workspace sends the bot a DM
- **THEN** the bot sends nothing to `/v1`

#### Scenario: The lookup fails
- **WHEN** `users.info` raises an error
- **THEN** the bot rejects the message with the general error text, and it looks up the user again for the next message

#### Scenario: A member becomes a guest
- **WHEN** a member asks one question, then becomes a guest and asks another
- **THEN** the bot answers the first question and rejects the second

### Requirement: A rejected user SHALL get the configured notice
The bot MUST send a rejected user `services.slack.external_user_text`, or the built-in text when the key is null, as one ephemeral message in the thread of the question. It MUST store no feedback click or form from a rejected user.

#### Scenario: The notice
- **WHEN** a guest mentions the bot in thread `5.0` of channel `C1`
- **THEN** the bot calls `chat.postEphemeral` once with that channel, user, text, and `thread_ts` `5.0`

#### Scenario: A rejected feedback click
- **WHEN** a guest clicks Helpful
- **THEN** the bot inserts no feedback row

### Requirement: The Slack bot SHALL stay only in allowed channels
When `services.slack.allowed_channels` lists channel IDs, the bot MUST NOT answer a mention in another channel. It MUST leave each unlisted channel at start-up and when it joins an unlisted channel. A direct message MUST stay allowed. When the list is empty or absent, the bot MUST NOT limit channels.

#### Scenario: An unlisted channel
- **WHEN** a user mentions the bot in a channel that is not on the list
- **THEN** the bot sends nothing to `/v1` and posts nothing

#### Scenario: Someone adds the bot to an unlisted channel
- **WHEN** the bot gets `member_joined_channel` for itself in an unlisted channel
- **THEN** the bot calls `conversations.leave` for that channel

#### Scenario: Start-up
- **WHEN** the bot starts and is a member of an unlisted channel
- **THEN** the bot leaves that channel before it connects to Socket Mode
