## Why

The `/v1` OpenAI-compatible API has two bugs identified during PR review (archi-physics/archi#551): the non-streaming path crashes on every successful request due to a type mismatch, and every request double-inserts messages into the `conversations` table because both `ChatWrapper._finalize_result()` and `openai_compat._persist_messages()` write independently.

## What Changes

- **Fix non-streaming response handling**: `_non_streaming_response` currently calls `response.answer` on the `final` event's `response` field, but `ChatWrapper.stream()` emits `response` as a plain `str` (returned by `_finalize_result`). This raises `AttributeError` on every successful non-streaming request. Change to treat `response` as a string directly.
- **Remove duplicate message persistence**: `_persist_messages()` inserts user+assistant messages into the `conversations` table, but `ChatWrapper.stream()` already does this in `_finalize_result()` → `insert_conversation()`. Remove the redundant `_persist_messages()` calls and the function itself.
- **Fix test mocks**: Update `test_openai_compat_endpoints.py` mocks that use `SimpleNamespace(answer=...)` to use plain strings, matching the real `ChatWrapper.stream()` contract.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `openai-compat`: Fix response handling to treat `final` event's `response` as a string, and remove duplicate message persistence that conflicts with `ChatWrapper`'s built-in persistence.

## Impact

- `src/interfaces/chat_app/openai_compat.py` — fix `_non_streaming_response`, remove `_persist_messages` function and all call sites
- `tests/unit/test_openai_compat_endpoints.py` — revert `SimpleNamespace` mocks back to plain strings
- `tests/unit/test_openai_compat_conversations.py` — remove or update tests for `_persist_messages` since the function will be removed
