## Context

The `/v1` OpenAI-compatible API (`openai_compat.py`) has two bugs from a mismatch between assumptions about `ChatWrapper.stream()` behavior and what it actually does:

1. `_non_streaming_response` accesses `response.answer` (line 306), assuming a `PipelineOutput` object. But `ChatWrapper.stream()` runs `_finalize_result()` which returns a plain `str` — so the `final` event's `response` field is always a string. This crashes every non-streaming request with `AttributeError: 'str' object has no attribute 'answer'`.

2. `_persist_messages()` inserts user+assistant messages into `conversations`. But `_finalize_result()` (called inside `ChatWrapper.stream()` before emitting the `final` event) already calls `insert_conversation()` to persist both messages. Result: every successful `/v1` request writes duplicate rows.

## Goals / Non-Goals

**Goals:**
- Fix non-streaming `/v1` requests so they return content instead of crashing
- Eliminate duplicate message rows in the `conversations` table
- Align test mocks with the real `ChatWrapper.stream()` contract

**Non-Goals:**
- Changing `ChatWrapper.stream()`'s behavior or return types
- Adding new features to the `/v1` API
- Addressing other PR review items (race condition, error leaks, etc.)

## Decisions

### 1. Treat `response` as a string, don't call `.answer`

**Decision**: Replace `final_content = response.answer` with `final_content = response` (since it's already a string).

**Alternative considered**: Make `ChatWrapper.stream()` emit the raw `PipelineOutput` instead of the finalized string. Rejected because that would change the contract for all consumers of `stream()`, not just `/v1`.

### 2. Remove `_persist_messages()` entirely

**Decision**: Delete the `_persist_messages` function and all 5 call sites. `ChatWrapper.stream()` → `_finalize_result()` → `insert_conversation()` already handles persistence for both successful and error cases.

**Alternative considered**: Keep `_persist_messages` but skip it when `conversation_id` is set. Rejected because `_finalize_result` always runs when a `final` event is emitted — there's no case where we need both.

**Note**: The citation text appended by `openai_compat.py` after the `final` event won't be in the persisted message (since `_finalize_result` runs first). This is acceptable — `_finalize_result` already appends its own source links via `format_links_markdown`.

### 3. Revert test mocks to plain strings

**Decision**: Change `SimpleNamespace(answer="...")` back to plain `"..."` in test mock events. This was introduced earlier in this session to match the `.answer` access pattern, but since we're removing that pattern, the mocks should match the real contract.

## Risks / Trade-offs

- **[Risk] Citation text not in DB** → Acceptable. `_finalize_result` already formats and appends source links to the persisted message. The `/v1` citation formatter produces a slightly different format, but the core content is preserved by `ChatWrapper`.
- **[Risk] Error-path persistence removed** → `_persist_messages` was the only thing persisting user messages on error. But `_finalize_result` only runs on success, so error-path messages were never duplicated anyway. On error, neither path persists (which is consistent with how the native chat app works).
