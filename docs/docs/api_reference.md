# API Reference

REST API endpoints for the Archi chat application. All endpoints are prefixed with `/api/`.

> **Note:** For the CLI reference, see [CLI Reference](cli_reference.md). For the configuration YAML schema, see [Configuration Reference](configuration.md).

## How to Read This Page

- Base URL is your running chat service (for example `http://localhost:7861`).
- Most `/api/*` endpoints require an authenticated session.
- Endpoints marked **Admin only** require an admin user.
- Authentication routes (`/login`, `/logout`, `/auth/user`) are not under `/api/`.

---

## Chat

### `POST /api/get_chat_response`

Send a message and receive a complete response.

**Request body** (JSON). Both chat endpoints take the same payload.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `last_message` | list of `[sender, message]` pairs | yes | The user's turn. A list **containing** the pair, not the pair itself — see below. Only the first pair is read. |
| `conversation_id` | int or `null` | no | Existing conversation to append to. `null` (or omitted) starts a new one. |
| `client_id` | string | yes | Identifies the calling client; the request is rejected without it. |
| `config_name` | string | no | Named configuration to answer under. |
| `is_refresh` | bool | no | Re-answer the previous turn instead of adding a new one. |
| `provider` | string | no | Override the LLM provider for this request only. |
| `model` | string | no | Override the model for this request only. |
| `include_agent_steps` | bool | no | Include agent reasoning steps in the response. Default `true`. |
| `include_tool_steps` | bool | no | Include tool invocations in the response. Default `true`. |
| `client_sent_msg_ts` | int (ms since epoch) | no | Client send time, used for latency accounting. |
| `client_timeout` | int (ms) | no | Client's own timeout, used for latency accounting. |

**`last_message` is nested.** It is a list whose first element is the
`[sender, message]` pair — `[["User", "How do I submit a job?"]]`, **not**
`["User", "How do I submit a job?"]`. The handler reads `last_message[0]` and unpacks
it as `(sender, content)`.

Sending the flat form does not return a clean error, so it is worth getting right:
the first element is then a *string*, and unpacking it yields its characters. A
sender of three or more characters raises and the request fails with HTTP 500; a
two-character sender such as `["AI", "hello"]` unpacks into `sender="A"`,
`content="I"` and the request **succeeds against the wrong content**, silently
discarding the message. The endpoint does not currently validate the shape.

```json
{
  "last_message": [["User", "How do I submit a job?"]],
  "conversation_id": null,
  "client_id": "web-3f2a91"
}
```

### `POST /api/get_chat_response_stream`

Send a message and receive a streaming response via NDJSON (`application/x-ndjson`).

Takes the same request body as `POST /api/get_chat_response` above, including the
nested `last_message` shape.

Each line is a JSON object with a `type` field. Event types:

| Type | Description |
|------|-------------|
| `meta` | Stream metadata (sent first, includes padding) |
| `text` | Response text delta |
| `tool_start` | Agent is invoking a tool |
| `tool_output` | Tool result |
| `thinking_start` | Reasoning model thinking begins |
| `thinking_end` | Reasoning model thinking ends |
| `final` | Final response with full message and metadata |
| `error` | Error occurred |

### `POST /api/cancel_stream`

Cancel an in-progress streaming response.

### `GET /api/trace/<trace_id>`

Retrieve the full trace of a previous request.

### `POST /api/ab/create`

Create an A/B comparison between two model responses.

---

## Authentication

Authentication routes are served at the application root (not under `/api/`).

### `GET|POST /login`

Authenticate with email and password. GET renders the login page; POST processes credentials.

### `GET /logout`

End the current session.

### `GET /auth/user`

Get the current authenticated user.

---

## User Management

### `GET /api/users/me`

Get or create the current user.

**Response:**
```json
{
  "id": "user_abc123",
  "display_name": "John Doe",
  "email": "john@example.com",
  "auth_provider": "basic",
  "theme": "dark",
  "preferred_model": "gpt-4o",
  "preferred_temperature": 0.7,
  "has_openrouter_key": true,
  "has_openai_key": false,
  "has_anthropic_key": false
}
```

### `PATCH /api/users/me/preferences`

Update user preferences (model, temperature, prompts, theme).

**Request:**
```json
{
  "theme": "light",
  "preferred_model": "claude-3-opus",
  "preferred_temperature": 0.5
}
```

### `PUT /api/users/me/api-keys/{provider}`

Set a BYOK API key. Provider: `openrouter`, `openai`, `anthropic`.

### `DELETE /api/users/me/api-keys/{provider}`

Delete a BYOK API key.

---

## Provider Keys (BYOK)

### `GET /api/providers/keys`

Get status of all provider API keys.

### `POST /api/providers/keys/set`

Set a session API key (validates before storing).

### `POST /api/providers/keys/clear`

Clear a session API key.

---

## Configuration

### `GET /api/config/static`

Get static (deploy-time) configuration.

**Response:**
```json
{
  "deployment_name": "my-archi",
  "embedding_model": "text-embedding-3-small",
  "available_pipelines": ["QAPipeline", "CMSCompOpsAgent"],
  "available_models": ["gpt-4o", "claude-3-opus"],
  "auth_enabled": true,
  "prompts_path": "/root/archi/data/prompts/"
}
```

### `GET /api/config/dynamic`

Get dynamic (runtime) configuration.

**Response:**
```json
{
  "active_pipeline": "QAPipeline",
  "active_model": "gpt-4o",
  "temperature": 0.7,
  "max_tokens": 4096,
  "top_p": 0.9,
  "top_k": 50,
  "num_documents_to_retrieve": 10,
  "verbosity": 3
}
```

### `PATCH /api/config/dynamic`

Update dynamic configuration. **Admin only.**

**Request:**
```json
{
  "active_model": "gpt-4o",
  "temperature": 0.8,
  "num_documents_to_retrieve": 5
}
```

### `GET /api/config/effective`

Get effective configuration for the current user (user preferences applied).

### `GET /api/config/audit`

Get configuration change audit log. **Admin only.**

**Query params:** `limit` (default: 100)

---

## Agents

### `GET /api/agents/list`

List all available agent specs.

### `GET /api/agents/spec`

Get a specific agent spec (name, tools, prompt). Pass `name` as a query parameter.

### `GET /api/agents/template`

Get the template for creating a new agent (available tools, defaults).

### `POST /api/agents`

Create or update an agent spec.

**Request:**
```json
{
  "name": "My Agent",
  "tools": ["search_vectorstore_hybrid", "fetch_catalog_document"],
  "prompt": "You are a helpful assistant..."
}
```

### `DELETE /api/agents`

Delete an agent spec. Pass `name` as a query parameter or in the request body.

### `POST /api/agents/active`

Set the active agent for the current session.

**Request:**
```json
{
  "agent_name": "CMS Comp Ops"
}
```

---

## Prompts

### `GET /api/prompts`

List all available prompts by type.

**Response:**
```json
{
  "condense": ["default", "concise"],
  "chat": ["default", "formal", "technical"],
  "system": ["default", "helpful"]
}
```

### `GET /api/prompts/{type}`

List prompts for a specific type.

### `GET /api/prompts/{type}/{name}`

Get prompt content.

### `POST /api/prompts/reload`

Reload prompt cache from disk. **Admin only.**

---

## Document Selection

Three-tier document selection: conversation override → user default → system default.

### `GET /api/documents/selection`

Get enabled documents. Query param: `conversation_id`.

### `PUT /api/documents/user-defaults`

Set user's default for a document.

**Request:**
```json
{
  "document_id": 42,
  "enabled": false
}
```

### `PUT /api/documents/conversation-override`

Set conversation-specific override.

### `DELETE /api/documents/conversation-override`

Clear conversation override (fall back to user default).

---

## Data Viewer

### `GET /api/data/documents`

List ingested documents with pagination and filtering.

**Query params:** `limit` (default: 100), `offset`, `search`, `source_type`

**Response:**
```json
{
  "documents": [
    {
      "hash": "5e90ca54526f3e11",
      "file_name": "readme.md",
      "source_type": "links",
      "chunk_count": 5,
      "enabled": true,
      "ingested_at": "2025-01-29T10:30:00Z"
    }
  ],
  "total": 42
}
```

### `GET /api/data/documents/<hash>/content`

Get document content and chunks.

### `POST /api/data/documents/<hash>/enable`

Enable a document for retrieval.

### `POST /api/data/documents/<hash>/disable`

Disable a document from retrieval.

### `POST /api/data/bulk-enable`

Enable multiple documents.

**Request:**
```json
{
  "hashes": ["5e90ca54526f3e11", "a1b2c3d4e5f67890"]
}
```

### `POST /api/data/bulk-disable`

Disable multiple documents.

### `GET /api/data/stats`

Get document statistics (total, enabled, disabled, by source type).

---

## Analytics

### `GET /api/analytics/model-usage`

Get model usage statistics. Query params: `start_date`, `end_date`, `service`.

### `GET /api/analytics/ab-comparisons`

Get A/B comparison statistics with win rates. Query params: `model_a`, `model_b`, `start_date`, `end_date`.

---

## Data Manager

These endpoints are served by the Data Manager service (default port: 7871).

### `GET /api/ingestion/status`

Get current ingestion progress.

### `POST /api/reload-schedules`

Trigger schedule reload from database.

### `GET /api/schedules`

Get current schedule status.

---

## Health & Info

### `GET /api/health`

Health check with database connectivity status.

### `GET /api/info`

Get API version and available features.
