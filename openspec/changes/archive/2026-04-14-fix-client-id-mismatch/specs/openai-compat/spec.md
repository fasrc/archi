## MODIFIED Requirements

### Requirement: Consistent client_id in conversation persistence
The `/v1/chat/completions` endpoint SHALL use a single `client_id` value for both conversation creation (`_get_or_create_conversation`) and pipeline invocation (`stream_kwargs`). The `client_id` SHALL be computed once per request as: `user_id` if authenticated, otherwise `f"v1_{uuid.uuid4().hex[:12]}"`.

#### Scenario: Authenticated user creates a new conversation
- **WHEN** an authenticated user sends a request with `X-OpenWebUI-Chat-Id` for a chat that has no existing archi conversation
- **THEN** the system creates a `conversation_metadata` row with `client_id` equal to the user's `user_id`, and passes the same `user_id` as `client_id` in `stream_kwargs`

#### Scenario: Unauthenticated user creates a new conversation
- **WHEN** an unauthenticated user sends a request with `X-OpenWebUI-Chat-Id` for a chat that has no existing archi conversation
- **THEN** the system generates a stable `v1_<hex>` client_id, stores it in `conversation_metadata`, and passes the same value in `stream_kwargs`

#### Scenario: Subsequent request for an existing conversation
- **WHEN** a request arrives with an `X-OpenWebUI-Chat-Id` that already maps to an archi conversation
- **THEN** the system returns the existing `conversation_id` and the pipeline accesses it without `ConversationAccessError`

### Requirement: _get_or_create_conversation accepts caller-provided client_id
The `_get_or_create_conversation()` function SHALL accept a `client_id` parameter and use it directly in the `INSERT INTO conversation_metadata` statement, rather than deriving `client_id` internally.

#### Scenario: client_id parameter is used in INSERT
- **WHEN** `_get_or_create_conversation(external_chat_id, user_id, client_id)` is called
- **THEN** the `client_id` column in the inserted row MUST equal the `client_id` argument
