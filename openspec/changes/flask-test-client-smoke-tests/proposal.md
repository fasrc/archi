## Why

The openwebui-compat-mode feature added `/v1/models` and `/v1/chat/completions` endpoints but has no tests that exercise the actual Flask HTTP layer. Existing tests cover citation formatting and conversation persistence logic in isolation, but nothing verifies the endpoint routing, request validation, auth middleware, SSE streaming format, or error responses through Flask's test client.

## What Changes

- Add a test file exercising `/v1` endpoints via Flask's test client with a mocked `ChatWrapper`
- Cover request validation, auth flows, streaming and non-streaming response formats, and error handling
- No production code changes — test-only

## Capabilities

### New Capabilities

- `v1-endpoint-smoke-tests`: Flask test client tests for the `/v1/models` and `/v1/chat/completions` endpoints, covering routing, request validation, auth middleware, streaming SSE format, non-streaming JSON format, and error responses

### Modified Capabilities

_(none)_

## Impact

- **New code**: `tests/unit/test_openai_compat_endpoints.py` (~200 lines)
- **Existing code**: No changes
- **Dependencies**: Uses Flask's built-in test client + unittest.mock — no new dependencies
