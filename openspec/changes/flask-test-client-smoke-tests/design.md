## Context

The `/v1` blueprint (`src/interfaces/chat_app/openai_compat.py`) registers Flask routes that depend on a `ChatWrapper` instance and `UserService` instance set via module-level globals during `register_openai_compat()`. The tests need to create a minimal Flask app, register the blueprint with mocked dependencies, and exercise endpoints through Flask's test client.

## Goals / Non-Goals

**Goals:**
- Test HTTP contract: status codes, content types, response shapes
- Test auth middleware: 401/403/bypass behavior
- Test streaming format: SSE `data:` lines, `[DONE]` terminator
- Test error responses: OpenAI-format JSON errors

**Non-Goals:**
- Test actual pipeline execution (mocked)
- Test database persistence (covered by existing tests)
- Performance or load testing

## Decisions

### 1. Use a Flask test app, not the full FlaskAppWrapper

**Decision:** Create a minimal `Flask()` app in the test fixture, register the `/v1` blueprint directly, and set module-level globals (`_chat_wrapper`, `_user_service`, `_auth_enabled`) to mocks.

**Rationale:** `FlaskAppWrapper` requires config files, PostgreSQL, and dozens of service initializations. A minimal app isolates the blueprint behavior.

### 2. Mock ChatWrapper.stream() to yield controlled events

**Decision:** The mock `ChatWrapper` yields a sequence of event dicts (chunk, final, error) that the test controls.

**Rationale:** This lets each test precisely define the streaming behavior without any LLM or pipeline dependency.

### 3. Test both auth-enabled and auth-disabled modes

**Decision:** Use separate fixtures or parameterized tests to cover both `_auth_enabled = True` and `_auth_enabled = False`.

**Rationale:** The auth middleware short-circuits when disabled, so both paths need coverage.
