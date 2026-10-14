## ADDED Requirements

### Requirement: Slack bot traces SHALL carry the Slack attributes
For a `/v1/chat/completions` request whose `X-OpenWebUI-Chat-Id` starts with `slack:`, the request span and every span started while the answer runs MUST carry `session.id` equal to the chat ID, `tag.tags` equal to `["slack"]`, and a `metadata` JSON object with `source` equal to `slack`. A request with any other chat ID, or with none, MUST carry none of these attributes.

#### Scenario: A Slack request
- **WHEN** the bot sends `X-OpenWebUI-Chat-Id: slack:T1:C1:1700000000.000100`
- **THEN** the spans of the answer carry `session.id` `slack:T1:C1:1700000000.000100`, the `slack` tag, and `metadata.slack_channel` `C1`

#### Scenario: Another client
- **WHEN** a request has the chat ID `owui-123` or no chat ID
- **THEN** no span of the answer carries the Slack attributes

#### Scenario: The consumer stops early
- **WHEN** the response stops before the answer stream ends
- **THEN** no Slack attribute stays attached to the context

### Requirement: The Slack bot SHALL open the root span of each turn
The bot MUST open a `Slack question` span of kind CHAIN, with the same attributes, around each `/v1` call. The span MUST carry the question and the answer text only when content capture is on.

#### Scenario: Sessions in Phoenix
- **WHEN** a user asks two questions in one Slack thread
- **THEN** both traces have a root span with the same `session.id`

#### Scenario: Content capture off
- **WHEN** content capture is off
- **THEN** the root span has no `input.value` and no `output.value`
