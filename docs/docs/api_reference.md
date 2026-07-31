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

**Request body** (JSON). Both chat endpoints accept the same payload, but they do not
honour all of it — the last four fields are read only by the streaming endpoint. See
[Fields the non-streaming endpoint ignores](#fields-the-non-streaming-endpoint-ignores).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `last_message` | list of `[sender, message]` pairs | yes | The user's turn. A list **containing** the pair, not the pair itself — see below. Only the first pair is read. |
| `client_id` | string | yes | Identifies the calling client; the request is rejected without it. |
| `client_sent_msg_ts` | int (ms since epoch) | **yes, in practice** | Time you send the request. Must be generated **at send time** — a stale value is rejected. See the warning below. |
| `client_timeout` | int (ms) | **yes, in practice** | How long the client is willing to wait. Omitting it is rejected. See the warning below. |
| `conversation_id` | int or `null` | no | Existing conversation to append to. `null` (or omitted) starts a new one. |
| `config_name` | string | no | Named configuration to answer under. |
| `is_refresh` | bool | no | Re-answer the previous turn instead of adding a new one. |
| `provider` | string | stream only, **with `model`** | Override the LLM provider. Has no effect unless `model` is sent too — see [Overriding provider and model](#overriding-provider-and-model). Ignored entirely by `POST /api/get_chat_response`. |
| `model` | string | stream only, **with `provider`** | Override the model. Has no effect unless `provider` is sent too — see [Overriding provider and model](#overriding-provider-and-model). Ignored entirely by `POST /api/get_chat_response`. |
| `include_agent_steps` | bool | stream only | Include the incremental **answer text** — the `chunk` events ([`app.py:2365`][chunkgate]). Default `true`. Does **not** gate reasoning. Ignored by `POST /api/get_chat_response`. |
| `include_tool_steps` | bool | stream only | Include tool events (`tool_start`, `tool_output`, `tool_end`) **and reasoning events** (`thinking_start`, `thinking_end`, [`app.py:2345`][thinkgate]). Default `true`. Ignored by `POST /api/get_chat_response`. |

!!! warning "Send both timing fields, and generate the timestamp fresh"

    `client_sent_msg_ts` and `client_timeout` look optional and are not. Both default to
    `0` when absent ([`app.py:4595-4596`][parse]), and the timeout check is an unguarded
    comparison ([`app.py:1654`][check]):

    ```python
    if server_received_msg_ts.timestamp() - client_sent_msg_ts > client_timeout:
        return None, 408
    ```

    Three ways to fall foul of it, all rejected:

    | You send | Effective values | Result |
    |---|---|---|
    | neither field | `0`, `0` | `<seconds since 1970> - 0 > 0` → rejected |
    | only `client_sent_msg_ts` | e.g. `1769900000.0`, `0` | anything `> 0` → rejected |
    | only `client_timeout` | `0`, e.g. `600.0` | `<seconds since 1970> > 600` → rejected |

    So send **both**. And generate `client_sent_msg_ts` **when you send**, not as a copied
    constant: it is compared against the server clock, so a timestamp older than
    `client_timeout` is treated as a request that already timed out. A hard-coded value
    works the day it is written and fails silently thereafter.

    **How the rejection reaches you differs by endpoint** — the check is shared, the
    reporting is not:

    - `POST /api/get_chat_response` returns **HTTP 408** with `{"error": ...}`.
    - `POST /api/get_chat_response_stream` returns **HTTP 200**, emits its opening `meta`
      line, and only then yields an NDJSON error event
      `{"type": "error", "status": 408, "message": ...}` before closing
      ([`app.py:2024`][streamerr]). A streaming client that checks only the HTTP status
      sees success. You must inspect the events.

    This is a bug in the handler, not the intended contract — the streaming loop applies
    the same check to the same variable but guards it, `if client_timeout and ...`
    ([`app.py:2101`][stream]), so `0` there means "no deadline" while here it means
    "deadline already passed". Tracked as
    [#175](https://github.com/fasrc/archi/issues/175); once fixed, both fields become
    genuinely optional and this warning goes away. Until then, this page documents what
    the endpoints actually do.

[parse]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L4595-L4596
[check]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L1654
[streamerr]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2024
[stream]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2101

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

**A request you can run.** `client_sent_msg_ts` has to be generated as you send, so this
example computes it rather than hard-coding one — a literal epoch value pasted from a page
like this is stale on arrival and comes back rejected:

```bash
curl -sS http://localhost:7861/api/get_chat_response \
  -H 'Content-Type: application/json' \
  -d "$(jq -nc --argjson ts "$(date +%s000)" '{
        last_message: [["User", "How do I submit a job?"]],
        conversation_id: null,
        client_id: "web-3f2a91",
        client_sent_msg_ts: $ts,
        client_timeout: 600000
      }')"
```

!!! note "It runs as-is only where authentication is disabled"

    Every chat route is registered through `require_auth` ([`app.py:2729`][authwrap]), so with
    `services.chat_app.auth.enabled: true` this command gets `401` — or a `302` to the login
    page when SSO is on and anonymous access is blocked — instead of an answer. Nothing about
    the request body is wrong in that case; it never reaches the handler.

    Against a deployment with **basic auth** enabled, log in first and reuse the session
    cookie (`/login` accepts a form-encoded `username` and `password`,
    [`app.py:3213`][loginform], and exists only when auth is enabled):

    ```bash
    curl -sS -c jar.txt -X POST http://localhost:7861/login \
      -d 'username=<user>&password=<password>'
    curl -sS -b jar.txt http://localhost:7861/api/get_chat_response \
      -H 'Content-Type: application/json' -d '<body as above>'
    ```

    With **SSO** the login is a browser redirect flow that curl cannot complete; copy the
    session cookie out of an already-logged-in browser session instead.

[authwrap]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2729
[loginform]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L3213

The body it builds has this shape. This is a **template, not valid JSON** — the placeholder
is deliberately unquoted so that pasting it unedited fails in your own JSON parser rather
than reaching the server, where a non-integer would surface as an opaque HTTP 500. Replace
it with the current time in milliseconds, as an integer:

```text
{
  "last_message": [["User", "How do I submit a job?"]],
  "conversation_id": null,
  "client_id": "web-3f2a91",
  "client_sent_msg_ts": <epoch ms at send time>,
  "client_timeout": 600000
}
```

#### Fields the non-streaming endpoint ignores

`provider`, `model`, `include_agent_steps` and `include_tool_steps` are accepted by both
endpoints but **only acted on by `POST /api/get_chat_response_stream`**. The non-streaming
handler never reads them off the parsed payload, so it cannot select a provider or model
and always applies the defaults for step inclusion. Sending them there is silently
ignored — no error, no warning.

To override the provider or model, or to control step inclusion, use the streaming
endpoint.

#### Overriding provider and model

`provider` and `model` are **jointly required**. The streaming path builds a request-local
pipeline only under `if provider and model` ([`app.py:2037`][override]), so sending one
without the other is not a partial override — it is no override at all, and the request is
answered by the default pipeline. This is silent: there is no error and no warning, and the
answer looks normal, so a caller who sends `model` alone can receive a reply from a model
they did not ask for.

| Sent | Effect |
|---|---|
| `provider` + `model` | override **attempted** for this request only — may still fall back, see below |
| `provider` alone | **ignored**, default pipeline answers |
| `model` alone | **ignored**, default pipeline answers |

Sending both is necessary but not sufficient: the override is an attempt, and it has three
distinct outcomes.

| Outcome | What you receive |
|---|---|
| Applied | the answer, with the response's reported model set to `provider/model` |
| Rejected — unknown provider/model, or overrides disabled | `{"type": "error", "status": 400, ...}` and the stream **ends** ([`app.py:2048`][ovrreject]) |
| Fell back — provider construction or request-local pipeline build failed | `{"type": "warning", "message": "Using default model: …"}`, then the **default pipeline answers** ([`app.py:2052`][ovrwarn], [`:2073`][ovrwarn2]) |

The fallback is the one to design for: the request succeeds, the stream looks normal, and the
only signal that a different model answered is a `warning` event you have to be reading for.
There is also a quieter case — if the active pipeline exposes no `agent_llm`, the override is
skipped with no `error` and no `warning` at all ([`app.py:2055`][ovrguard]).

So do not infer the answering model from your own request. Read the reported model back from
the response, and treat a `warning` event as "my override did not take".

[ovrreject]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2048
[ovrwarn]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2052
[ovrwarn2]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2073
[ovrguard]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2055

[override]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2037

### `POST /api/get_chat_response_stream`

Send a message and receive a streaming response via NDJSON (`application/x-ndjson`).

Takes the same request body as `POST /api/get_chat_response` above, including the
nested `last_message` shape and the two required timing fields.

This is the endpoint that honours `provider`, `model`, `include_agent_steps` and
`include_tool_steps`; the non-streaming one ignores all four. `provider` and `model` must
be sent [together](#overriding-provider-and-model) or neither takes effect.

!!! warning "Once the stream opens, failures arrive as events — not as status codes"

    This endpoint has **two** error channels. Which one you get depends on whether the
    failure happens before or after the response is constructed at
    [`app.py:4768`][streamopen] — not on the kind of error.

    **Before the stream opens — an ordinary HTTP status.** Check these as you would on any
    endpoint:

    | Failure | Status |
    |---|---|
    | Not authenticated, SSO on and anonymous access blocked | **302** redirect to login |
    | Not authenticated, otherwise | **401** `{"error": "Unauthorized"}` |
    | `client_id` missing ([`app.py:4730`][clientid]) | **400** `{"error": "client_id missing"}` |

    **After the stream opens — HTTP 200 plus an event.** The status line is already on the
    wire, so a failure inside the generator can only be reported in-band: the opening `meta`
    line, then

    ```json
    {"type": "error", "status": 408, "message": "..."}
    ```

    The timeout rejection described above is in this second group.

    A correct client therefore does **both**: check the HTTP status first, and — when it is
    `200` — still inspect the events for `type: "error"`, whose `status` field holds what the
    non-streaming endpoint would have returned. Treating `200 OK` alone as success misses the
    second group; ignoring the status code misses the first.

[streamopen]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L4768
[clientid]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L4730

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
| `warning` | The request continued, but not as asked — e.g. an override fell back to the default model |

!!! warning "The two step flags do not group events the way their names suggest"

    Read this before using either flag to filter the stream. The grouping is by
    **flag**, not by the sense of the flag's name:

    | Flag | Actually gates |
    |---|---|
    | `include_agent_steps` | the incremental answer text — `chunk` events ([`app.py:2365`][chunkgate], [`:2399`][chunkgate2]) |
    | `include_tool_steps` | `tool_start`, `tool_output`, `tool_end` **and** `thinking_start` / `thinking_end` ([`app.py:2345`][thinkgate], [`:2359`][thinkgate2]) |

    So reasoning events are controlled by the **tool** flag. Setting
    `include_agent_steps: false` to suppress reasoning does the opposite of what you
    want twice over: the `thinking_*` events still arrive, and you silently stop
    receiving streamed answer text. The `final` event still carries the complete
    answer, so the loss shows up as an answer that appears all at once at the end
    rather than as an error.

    To suppress reasoning, set `include_tool_steps: false` — accepting that tool
    events go with it. The two are not separable through this API.

[chunkgate]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2365
[chunkgate2]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2399
[thinkgate]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2345
[thinkgate2]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2359

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
