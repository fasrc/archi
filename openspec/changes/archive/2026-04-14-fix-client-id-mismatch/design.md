## Context

The `/v1/chat/completions` endpoint in `openai_compat.py` creates conversations via `_get_or_create_conversation()` and then passes request parameters to `ChatWrapper.stream()`. The `client_id` column in `conversation_metadata` is used by the ChatWrapper pipeline to authorize access to conversations -- a mismatch between the `client_id` used at creation time and the one passed in `stream_kwargs` causes `ConversationAccessError`.

Prior to the fix, `_get_or_create_conversation()` used `user_id` as the `client_id` column value, while `chat_completions()` independently generated `client_id = user_id or f"v1_{uuid.uuid4().hex[:12]}"` for `stream_kwargs`. For unauthenticated users (`user_id=None`), the INSERT wrote `NULL` for `client_id` while the stream received a random `v1_*` string -- guaranteed mismatch.

## Goals / Non-Goals

**Goals:**
- Ensure the same `client_id` value is used for both conversation creation and pipeline access within a single request.
- Maintain a stable, non-null `client_id` for unauthenticated `/v1` requests.

**Non-Goals:**
- Migrating or repairing orphaned conversations created before the fix.
- Changing the `client_id` semantics for the native chat app interface.
- Adding persistent client identity across requests for unauthenticated users.

## Decisions

**Single `client_id` computation point**: Compute `client_id` once in `chat_completions()` and pass it as an argument to `_get_or_create_conversation()`. This is the simplest change that eliminates the mismatch -- no new abstractions, no shared state.

*Alternative considered*: Have `_get_or_create_conversation()` return the `client_id` it used, then feed that into `stream_kwargs`. Rejected because it couples the return value to an internal detail and doesn't address the unauthenticated case where `user_id` is `None`.

## Risks / Trade-offs

- **Orphaned rows**: Conversations created before the fix have mismatched `client_id` values and will remain inaccessible via the pipeline. This is acceptable -- those conversations were already broken. No migration is planned.
- **Unauthenticated `client_id` is ephemeral**: Each request from an unauthenticated user gets a fresh `v1_<hex>` value, so conversations cannot be resumed across requests without the `X-OpenWebUI-Chat-Id` header. This is by design -- the external chat ID is the durable key.
