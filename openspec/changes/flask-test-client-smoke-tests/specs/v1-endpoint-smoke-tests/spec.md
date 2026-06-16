## ADDED Requirements

### Requirement: GET /v1/models returns valid model list
The test SHALL verify that `GET /v1/models` returns HTTP 200 with a JSON body containing `object: "list"` and a `data` array of model objects with `id`, `object`, `created`, and `owned_by` fields.

#### Scenario: Successful model list
- **WHEN** a GET request is sent to `/v1/models`
- **THEN** the response is HTTP 200 with `Content-Type: application/json`
- **AND** the body contains `{"object": "list", "data": [{"id": "...", "object": "model", ...}]}`

---

### Requirement: POST /v1/chat/completions validates input
The test SHALL verify that missing or invalid request fields produce appropriate HTTP error responses in OpenAI error format.

#### Scenario: Missing model field
- **WHEN** a POST to `/v1/chat/completions` omits `model`
- **THEN** the response is HTTP 400 with `{"error": {"message": "...", "type": "invalid_request_error"}}`

#### Scenario: Unknown model
- **WHEN** a POST includes `model: "nonexistent"`
- **THEN** the response is HTTP 404

#### Scenario: Missing messages
- **WHEN** a POST includes `model` but omits `messages`
- **THEN** the response is HTTP 400

---

### Requirement: Non-streaming response format
The test SHALL verify that a non-streaming request returns a complete JSON response with the correct OpenAI shape.

#### Scenario: Valid non-streaming request
- **WHEN** a POST with `stream: false` completes successfully
- **THEN** the response is HTTP 200 with `choices[0].message.content` and `finish_reason: "stop"`

---

### Requirement: Streaming response format
The test SHALL verify that a streaming request returns SSE-formatted chunks terminated by `[DONE]`.

#### Scenario: Valid streaming request
- **WHEN** a POST with `stream: true` completes successfully
- **THEN** the response has `Content-Type: text/event-stream`
- **AND** each line starts with `data: ` followed by valid JSON
- **AND** the stream ends with `data: [DONE]`

---

### Requirement: Auth middleware enforcement
The test SHALL verify that bearer token authentication is enforced when enabled and bypassed when disabled.

#### Scenario: No token when auth enabled
- **WHEN** auth is enabled and no `Authorization` header is sent
- **THEN** the response is HTTP 401

#### Scenario: Invalid token when auth enabled
- **WHEN** auth is enabled and an invalid token is sent
- **THEN** the response is HTTP 401

#### Scenario: Auth disabled bypasses check
- **WHEN** auth is disabled
- **THEN** requests without tokens are processed normally

---

### Requirement: Pipeline error handling
The test SHALL verify that pipeline errors produce appropriate OpenAI-format error responses.

#### Scenario: Pipeline raises exception
- **WHEN** `ChatWrapper.stream()` raises an exception during a non-streaming request
- **THEN** the response is HTTP 500 with `{"error": {"message": "...", "type": "server_error"}}`
