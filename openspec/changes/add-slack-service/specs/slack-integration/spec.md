## ADDED Requirements

### Requirement: Answer mentions and direct messages through the /v1 API
The Slack bot SHALL answer an `app_mention` event, or a `message` event in a direct-message channel, by sending the question to the chat app's `POST /v1/chat/completions` with `stream: false` and posting the returned `choices[0].message.content` in the Slack thread of the question.

#### Scenario: Channel mention gets a threaded answer
- **WHEN** a user posts `<@BOT> what is the scratch quota?` at the top level of a channel
- **THEN** the bot sends `what is the scratch quota?` as the last `user` message to `/v1/chat/completions`
- **AND** the answer appears in the thread whose `thread_ts` is the mention's `ts`

#### Scenario: Direct message gets a threaded answer
- **WHEN** a user sends a direct message to the bot
- **THEN** the bot answers in the thread of that message

### Requirement: Ignore messages the bot must not answer
The Slack bot SHALL NOT answer a message that the bot itself wrote, a message that has any `subtype`, a `message` event outside a direct-message channel, or a message whose text is empty after mentions are removed.

#### Scenario: Own message is ignored
- **WHEN** a `message` event arrives whose `user` is the bot's own user ID
- **THEN** the bot sends no request to `/v1`

#### Scenario: Edited message is ignored
- **WHEN** a `message` event arrives with `subtype: message_changed`
- **THEN** the bot sends no request to `/v1`

#### Scenario: Channel message without a mention is ignored
- **WHEN** a `message` event arrives with `channel_type: channel`
- **THEN** the bot sends no request to `/v1`

### Requirement: One archi conversation per Slack thread
The Slack bot SHALL send the header `X-OpenWebUI-Chat-Id: slack:{team_id}:{channel}:{thread_ts}` on every `/v1` request, so that all questions in one Slack thread map to one archi conversation.

#### Scenario: Two questions in one thread share a key
- **WHEN** a user asks two questions in the same thread
- **THEN** both `/v1` requests carry the same `X-OpenWebUI-Chat-Id` value

### Requirement: Earlier thread turns are sent as history
The Slack bot SHALL send the thread messages older than the current question, in time order, as `messages` before the question, mapping the bot's own messages to `assistant` and all others to `user`, with mentions removed, empty texts dropped, and at most `history_limit` turns kept (the newest).

#### Scenario: Follow-up carries the earlier answer
- **WHEN** a thread holds a user question, then a bot answer, and the user asks a follow-up
- **THEN** the `/v1` request `messages` are `user`, `assistant`, `user` in that order

#### Scenario: Newer messages are excluded
- **WHEN** the thread holds a message newer than the question, such as the bot's placeholder
- **THEN** that message is not in the request `messages`

### Requirement: Acknowledge each Socket Mode request before work
The Slack bot SHALL send the Socket Mode acknowledgement for a request before it does any filtering, network call, or answer work, and an exception in the answer work SHALL NOT propagate into the socket listener.

#### Scenario: Acknowledgement precedes the answer
- **WHEN** an `events_api` request arrives
- **THEN** the first call the listener makes is `send_socket_mode_response` with the request's `envelope_id`

#### Scenario: A failed answer is reported in the thread
- **WHEN** the `/v1` call returns HTTP 500 with an `error.message`
- **THEN** the bot replaces its placeholder in the thread with one short error line
- **AND** the listener returns without raising

### Requirement: Each Slack message is answered at most once
The Slack bot SHALL answer each `(channel, ts)` pair at most once, also when Slack delivers the same message again or as both `message` and `app_mention`.

#### Scenario: Retried delivery is not answered twice
- **WHEN** the same event arrives twice with different `envelope_id` values
- **THEN** the bot sends exactly one request to `/v1`

### Requirement: Answers are converted to Slack mrkdwn
The Slack bot SHALL convert the answer from Markdown to Slack mrkdwn outside code fences (`**x**` to `*x*`, `[t](u)` to `<u|t>`, a `#` heading line to a bold line), SHALL leave code fences unchanged, and SHALL cut the text to at most 39,000 characters with a visible marker.

#### Scenario: Bold sources header is converted
- **WHEN** the answer ends with `**Sources:**`
- **THEN** the posted text ends with `*Sources:*`

#### Scenario: Code fence is unchanged
- **WHEN** the answer holds a code fence that contains `**x**`
- **THEN** the posted code fence still contains `**x**`

### Requirement: Startup proves the chat API is reachable
At startup the Slack bot SHALL call `GET /v1/models` with a bounded number of attempts, SHALL use the first returned model ID as `model`, and SHALL exit with a non-zero status if no attempt succeeds.

#### Scenario: Chat API comes up late
- **WHEN** the first two `/v1/models` calls fail and the third returns a model `my_archi`
- **THEN** the bot uses `model: my_archi` for its requests

#### Scenario: Chat API never comes up
- **WHEN** every attempt fails
- **THEN** startup raises an error that names `services.chat_app.openai_compat.enabled`

### Requirement: Tokens are never logged
The Slack bot SHALL read `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, and the optional `ARCHI_API_TOKEN` with `read_secret` and SHALL NOT write any token value to a log.

#### Scenario: Startup log holds no token
- **WHEN** the bot starts with tokens `xoxb-secret`, `xapp-secret`, and `archi_secret`
- **THEN** no log record contains `xoxb-secret`, `xapp-secret`, or `archi_secret`

#### Scenario: Bearer header only when a token is set
- **WHEN** `ARCHI_API_TOKEN` is empty
- **THEN** the `/v1` request has no `Authorization` header
