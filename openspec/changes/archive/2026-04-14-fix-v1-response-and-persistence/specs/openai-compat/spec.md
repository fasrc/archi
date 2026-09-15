## MODIFIED Requirements

### Requirement: Non-streaming response uses final event content directly
The `/v1/chat/completions` non-streaming path SHALL treat the `final` event's `response` field as a plain string containing the complete assistant reply. It MUST NOT assume `response` is an object with an `.answer` attribute.

#### Scenario: Successful non-streaming request
- **WHEN** a non-streaming `/v1/chat/completions` request receives a `final` event with `response` as a string
- **THEN** the response string SHALL be used directly as `choices[0].message.content`

#### Scenario: Response field is falsy
- **WHEN** a non-streaming `/v1/chat/completions` request receives a `final` event with `response` as an empty string or None
- **THEN** `choices[0].message.content` SHALL be an empty string

## REMOVED Requirements

### Requirement: _persist_messages writes to conversations table
**Reason**: `ChatWrapper.stream()` already persists both user and assistant messages via `_finalize_result()` → `insert_conversation()`. The separate `_persist_messages()` function causes duplicate rows in the `conversations` table for every successful `/v1` request.
**Migration**: Remove `_persist_messages()` and all call sites. No replacement needed — `ChatWrapper` handles persistence.

### Requirement: Test mocks use SimpleNamespace for response
**Reason**: Test mocks used `SimpleNamespace(answer=...)` to match the incorrect `.answer` access pattern. With the response now treated as a plain string, mocks SHALL use plain strings to match the real `ChatWrapper.stream()` contract.
**Migration**: Replace `SimpleNamespace(answer="...")` with `"..."` in all test mock `final` events. Remove `SimpleNamespace` import if no longer used.
