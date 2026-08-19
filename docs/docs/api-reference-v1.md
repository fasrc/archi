# /v1 API Reference

archi's OpenAI-compatible API. Enabled via `services.chat_app.openai_compat.enabled: true`.

## Authentication

When archi authentication is enabled, all `/v1` endpoints require a bearer token:

```
Authorization: Bearer archi_<token>
```

Generate tokens via `POST /api/users/me/api-token`.

When authentication is disabled, no token is required.

## Endpoints

### GET /v1/models

List available archi configurations as OpenAI model objects.

**Response:**

```json
{
  "object": "list",
  "data": [
    {
      "id": "my_archi",
      "object": "model",
      "created": 1700000000,
      "owned_by": "archi"
    }
  ]
}
```

### POST /v1/chat/completions

Send a chat completion request. Supports both streaming and non-streaming modes.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Config name from `/v1/models` |
| `messages` | array | Yes | Array of `{role, content}` objects |
| `stream` | boolean | No | Enable SSE streaming (default: `false`) |

**Example request:**

```json
{
  "model": "my_archi",
  "messages": [
    {"role": "user", "content": "What is RAG?"}
  ],
  "stream": true
}
```

**Non-streaming response:**

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "my_archi",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "RAG stands for..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

**Streaming response:**

Each event is an SSE line:

```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1700000000,"model":"my_archi","choices":[{"index":0,"delta":{"content":"RAG"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1700000000,"model":"my_archi","choices":[{"index":0,"delta":{"content":" stands"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1700000000,"model":"my_archi","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

**Headers:**

| Header | Description |
|--------|-------------|
| `X-OpenWebUI-Chat-Id` | Optional. Maps to an archi conversation for persistence across requests. |

## Error Format

All errors use the OpenAI error format:

```json
{
  "error": {
    "message": "Model 'unknown' not found. Available: my_archi",
    "type": "invalid_request_error"
  }
}
```

| Status | Type | When |
|--------|------|------|
| 401 | `invalid_request_error` | Missing or invalid token |
| 403 | `invalid_request_error` | User lacks `chat:query` permission |
| 404 | `invalid_request_error` | Unknown model |
| 500 | `server_error` | Pipeline execution error |

## Event Translation

archi's native streaming events are translated to OpenAI format:

| archi event | OpenAI SSE | Notes |
|-------------|------------|-------|
| `chunk` | `delta.content` | Text content |
| `final` | Citations + `finish_reason: "stop"` | Sources appended as markdown |
| `tool_start` | _(dropped)_ | No OpenAI equivalent |
| `thinking_start` | _(dropped)_ | No OpenAI equivalent |
| `error` | `delta.content: "[Error: ...]"` | Mid-stream errors |

The native `final` event carries two answer fields: `response`, the finalized text as the
native chat UI renders it (it ends with the UI's own appended source list), and `answer`, the
bare pipeline answer without that list. `answer` is omitted — never emitted as an empty
placeholder — when the pipeline produced no answer value. Non-streaming `/v1` responses build
their message content from `answer`, so the endpoint controls citation formatting itself (see
below).

## Source Citations

When the pipeline returns source documents, a citation block is appended to the response:

```markdown
---
**Sources:**
- `guide.md` [collection-name] (relevance: 0.92)
- `document.md` (relevance: 0.85)
```

Sources are ordered by relevance, highest first; a higher score means a closer match. When the
same document contributes more than one chunk it is listed once, at its best score. Sources the
pipeline returned without a score are listed last, without a `(relevance: ...)` suffix.

Collection labels appear only when sources span multiple collections.

A `/v1` response carries **at most one** source list, and never two. Which text the endpoint
returns depends only on whether the `final` event carried the bare `answer` field, never on what
retrieval returned. On the normal path the content is that answer plus one citation block in the
format above, and the native chat UI's own source list (a `Show all sources` details block) does
not appear; streaming responses append the same citation block after the streamed chunks. If a
`final` event ever arrives without the `answer` field, the endpoint returns the finalized
`response` unchanged and appends no citation block, so the content carries at most the chat UI's
own list.

Either path can come back with no source list at all, even when retrieval found documents: the
citation block is empty when no document carries a usable display name, and the chat UI's list is
empty when its own relevance and visibility filtering leaves nothing to show.
