## 1. Fix non-streaming response handling

- [x] 1.1 In `_non_streaming_response`, replace `final_content = response.answer` with `final_content = response or ""` to treat response as a plain string

## 2. Remove duplicate persistence

- [x] 2.1 Remove the `_persist_messages` function from `openai_compat.py`
- [x] 2.2 Remove all `_persist_messages()` call sites in `_streaming_response` (3 calls) and `_non_streaming_response` (2 calls)

## 3. Fix test mocks

- [x] 3.1 In `test_openai_compat_endpoints.py`, replace `SimpleNamespace(answer="...")` with plain strings in all mock `final` events and remove the `SimpleNamespace` import
- [x] 3.2 Remove or update `TestMessagePersistence` tests in `test_openai_compat_conversations.py` that tested `_persist_messages` behavior

## 4. Verify

- [x] 4.1 Run unit tests and confirm all openai-compat tests pass
