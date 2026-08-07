## 1. Fix client_id computation

- [x] 1.1 Add `client_id` parameter to `_get_or_create_conversation(external_chat_id, user_id, client_id)` and use it in the INSERT statement instead of `user_id`
- [x] 1.2 Compute `client_id` once in `chat_completions()` as `user_id or f"v1_{uuid.uuid4().hex[:12]}"` and pass it to both `_get_or_create_conversation()` and `stream_kwargs`

## 2. Verification

- [x] 2.1 Deploy and confirm an unauthenticated multi-turn conversation completes without `ConversationAccessError`
- [x] 2.2 Verify `conversation_metadata` row has matching `client_id` for the created conversation
