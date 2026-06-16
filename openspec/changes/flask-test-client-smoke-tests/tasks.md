## 1. Test Setup and Fixtures

- [x] 1.1 Create `tests/unit/test_openai_compat_endpoints.py` with test fixtures: minimal Flask app, mock `ChatWrapper` that yields controlled events, mock `UserService` with configurable token lookup. Set up `register_openai_compat()` with mocks.

## 2. GET /v1/models Tests

- [x] 2.1 Test `GET /v1/models` returns 200 with valid model list JSON (object, data array, model fields).
- [x] 2.2 Test `GET /v1/models` returns 401 when auth enabled and no token.

## 3. POST /v1/chat/completions Validation Tests

- [x] 3.1 Test missing `model` field returns 400 with OpenAI error format.
- [x] 3.2 Test unknown model returns 404.
- [x] 3.3 Test missing `messages` returns 400.

## 4. Non-Streaming Response Tests

- [x] 4.1 Test valid non-streaming request returns 200 with `choices[0].message.content` and `finish_reason: "stop"`.

## 5. Streaming Response Tests

- [x] 5.1 Test valid streaming request returns `Content-Type: text/event-stream`, SSE `data:` lines with valid JSON, ends with `data: [DONE]`.

## 6. Auth Middleware Tests

- [x] 6.1 Test no token when auth enabled returns 401.
- [x] 6.2 Test invalid token when auth enabled returns 401.
- [x] 6.3 Test auth disabled allows requests without tokens.

## 7. Error Handling Tests

- [x] 7.1 Test pipeline exception during non-streaming request returns 500 with OpenAI error format.
