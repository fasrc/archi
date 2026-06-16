## ADDED Requirements

### Requirement: Conversation mapping via external chat ID
The system SHALL map Open WebUI's conversation identifier (`X-OpenWebUI-Chat-Id` header) to archi's internal conversation. On first sight of an external chat ID, the system SHALL create a new archi conversation and store the mapping. On subsequent requests with the same external chat ID, the system SHALL resume the existing archi conversation.

#### Scenario: First message with external chat ID
- **WHEN** a `/v1/chat/completions` request includes `X-OpenWebUI-Chat-Id: abc-123` and no archi conversation is mapped to `abc-123`
- **THEN** a new conversation is created in `conversation_metadata` with `external_chat_id = 'abc-123'` and the user's ID

#### Scenario: Subsequent message with same chat ID
- **WHEN** a `/v1/chat/completions` request includes `X-OpenWebUI-Chat-Id: abc-123` and an archi conversation already exists for `abc-123`
- **THEN** the system resumes the existing conversation (appends messages, updates `last_message_at`)

#### Scenario: No external chat ID header
- **WHEN** a `/v1/chat/completions` request has no `X-OpenWebUI-Chat-Id` header
- **THEN** the system creates a new one-off conversation for this request (fallback for non-Open WebUI clients)

#### Scenario: Anonymous user conversation
- **WHEN** auth is disabled and a `/v1` request is processed
- **THEN** the conversation is created with a generated client_id (consistent per connection)

---

### Requirement: Message persistence
The system SHALL persist both the user message and archi's response in archi's `conversation_messages` table for every `/v1/chat/completions` request.

#### Scenario: User and assistant messages stored
- **WHEN** a `/v1/chat/completions` request completes successfully
- **THEN** the user's query is stored as a message with sender "user" and archi's response is stored with sender "archi", both linked to the conversation

#### Scenario: Streaming response persisted after completion
- **WHEN** a streaming `/v1/chat/completions` request completes
- **THEN** the full accumulated response text (not individual chunks) is persisted as a single assistant message

#### Scenario: Error does not persist partial response
- **WHEN** a `/v1/chat/completions` request fails mid-stream
- **THEN** the user message is persisted but no assistant message is stored

---

### Requirement: Independence from frontend history
archi's conversation persistence SHALL operate independently of any frontend's conversation storage. The `/v1` endpoint SHALL NOT attempt to sync with or deduplicate against Open WebUI's conversation database.

#### Scenario: Dual storage
- **WHEN** a user sends a message through Open WebUI which routes to archi's `/v1`
- **THEN** both Open WebUI and archi independently store the conversation in their own databases
