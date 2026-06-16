## ADDED Requirements

### Requirement: List models endpoint
The system SHALL expose `GET /v1/models` returning all available archi configurations (agent configs and, when multi-collection routing is present, collections and collection groups) as OpenAI model objects.

Each model object SHALL include `id` (string), `object` (literal `"model"`), `created` (integer Unix timestamp), and `owned_by` (literal `"archi"`).

The response SHALL have the shape `{"object": "list", "data": [...]}`.

#### Scenario: Single default config
- **WHEN** archi has one agent config named "default"
- **THEN** `GET /v1/models` returns `{"object": "list", "data": [{"id": "default", "object": "model", "created": <timestamp>, "owned_by": "archi"}]}`

#### Scenario: Multiple configs
- **WHEN** archi has configs "default", "research-agent", and "grading"
- **THEN** `GET /v1/models` returns all three as model objects in the data array

#### Scenario: No auth header
- **WHEN** a request to `GET /v1/models` has no `Authorization` header and auth is enabled
- **THEN** the system returns HTTP 401

---

### Requirement: Chat completions endpoint
The system SHALL expose `POST /v1/chat/completions` accepting the OpenAI chat completions request format and returning responses in OpenAI format.

The endpoint SHALL accept at minimum: `model` (string), `messages` (array of `{role, content}` objects), `stream` (boolean), `temperature` (float), and `max_tokens` (integer).

#### Scenario: Non-streaming request
- **WHEN** a client sends `POST /v1/chat/completions` with `stream: false`
- **THEN** the system returns a complete JSON response with `choices[0].message.content` containing the answer and `choices[0].finish_reason` set to `"stop"`

#### Scenario: Streaming request
- **WHEN** a client sends `POST /v1/chat/completions` with `stream: true`
- **THEN** the system returns `Content-Type: text/event-stream` and streams SSE chunks in the format `data: {"choices": [{"delta": {"content": "..."}}]}\n\n`
- **AND** the stream ends with `data: [DONE]\n\n`

#### Scenario: Unknown model
- **WHEN** a client sends a request with a `model` value that does not match any archi config
- **THEN** the system returns HTTP 404 with an OpenAI-format error: `{"error": {"message": "...", "type": "invalid_request_error"}}`

---

### Requirement: Request parameter mapping
The system SHALL map OpenAI request parameters to archi pipeline kwargs:
- `model` → `config_name`
- `messages[-1].content` → `query`
- `messages[:-1]` → `history`
- `temperature` → temperature override (if provided)
- `max_tokens` → max_tokens override (if provided)

Parameters not supported by archi SHALL be silently ignored.

#### Scenario: Temperature override
- **WHEN** a client sends `temperature: 0.2` in the request
- **THEN** the pipeline executes with temperature 0.2 instead of the config default

#### Scenario: History from messages
- **WHEN** a client sends 5 messages (alternating user/assistant)
- **THEN** the last user message becomes the query and the preceding 4 messages become conversation history

---

### Requirement: Response event translation
The system SHALL translate archi's NDJSON streaming events to OpenAI SSE delta format:
- `{"type": "chunk", "content": "..."}` → `data: {"choices": [{"delta": {"content": "..."}}]}`
- `{"type": "final", ...}` → `data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}`
- `{"type": "tool_start", ...}` → omitted from SSE stream
- `{"type": "thinking_start", ...}` → omitted from SSE stream
- `{"type": "error", ...}` → `data: {"choices": [{"delta": {"content": "[Error: ...]"}}]}`

#### Scenario: Text content streaming
- **WHEN** archi emits three chunk events with content "The ", "answer ", "is..."
- **THEN** the SSE stream contains three corresponding `data:` lines with `delta.content` for each

#### Scenario: Tool events omitted
- **WHEN** archi emits `tool_start` and `tool_end` events during agent execution
- **THEN** these events do not appear in the SSE stream

#### Scenario: Final event with sources
- **WHEN** archi emits a `final` event with `source_documents`
- **THEN** the SSE stream emits a citation chunk (per inline-citations spec) followed by a finish_reason stop chunk followed by `data: [DONE]`

---

### Requirement: Error responses
The system SHALL return errors in OpenAI-compatible format: `{"error": {"message": "...", "type": "...", "code": "..."}}` with appropriate HTTP status codes.

#### Scenario: Pipeline execution error
- **WHEN** archi's pipeline raises an exception during execution
- **THEN** the system returns HTTP 500 with `{"error": {"message": "<error details>", "type": "server_error"}}`

#### Scenario: Rate limit or overload
- **WHEN** the system cannot process the request due to load
- **THEN** the system returns HTTP 429 with `{"error": {"message": "...", "type": "rate_limit_error"}}`
