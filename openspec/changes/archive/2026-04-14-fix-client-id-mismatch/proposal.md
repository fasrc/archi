## Why

When the `/v1/chat/completions` endpoint creates a conversation, it stores `user_id` as the `client_id` in `conversation_metadata`. But subsequent stream calls pass a separately generated `client_id` (e.g., `v1_<hex>`), causing `ConversationAccessError` when the pipeline tries to look up the conversation. This fragments the audit trail and breaks multi-turn persistence for unauthenticated users.

## What Changes

- Compute a single stable `client_id` once per request in `chat_completions()` and thread it through both `_get_or_create_conversation()` and `stream_kwargs`.
- Update `_get_or_create_conversation()` to accept and use the caller-provided `client_id` instead of deriving its own.

## Capabilities

### New Capabilities

_None_ -- this is a bug fix within an existing capability.

### Modified Capabilities

- `openai-compat`: The `/v1` conversation persistence logic is corrected so that `client_id` is consistent between conversation creation and pipeline access.

## Impact

- **Code**: `src/interfaces/chat_app/openai_compat.py` -- `chat_completions()`, `_get_or_create_conversation()`.
- **Data**: Existing conversations created with the mismatched `client_id` will remain orphaned (no migration needed -- they were already inaccessible). New conversations will be correctly keyed.
- **APIs**: No external API changes; the fix is internal plumbing only.
