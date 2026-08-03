# API Reference

REST API endpoints for the Archi chat application. All endpoints are prefixed with `/api/`.

> **Note:** For the CLI reference, see [CLI Reference](cli_reference.md). For the configuration YAML schema, see [Configuration Reference](configuration.md).

## How to Read This Page

- Base URL is your running chat service (for example `http://localhost:7861`).
- Most `/api/*` endpoints require an authenticated session.
- Endpoints marked **Admin only** require an admin user.
- Authentication routes (`/login`, `/logout`, `/auth/user`) are not under `/api/`.
- **Lists of events, error statuses and failure modes describe categories and the cases you
  will meet — they are not closed enumerations.** The chat handler has behaviour that varies
  by pipeline and by provider, so this page documents the *mechanism* that decides an outcome
  and gives examples. Build clients that tolerate an event type or status they have not seen
  before, and prefer reading what the response reports over inferring it from the request.

---

## Chat

### `POST /api/get_chat_response`

Send a message and receive a complete response.

**Request body** (JSON). Both chat endpoints accept the same payload, but they do not
honour all of it — the last four fields are read only by the streaming endpoint. See
[Fields the non-streaming endpoint ignores](#fields-the-non-streaming-endpoint-ignores).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `last_message` | list of `[sender, message]` pairs | yes | The user's turn. A list **containing** the pair, not the pair itself — see below. Only the first pair is read. A malformed value is rejected with **HTTP 400**. |
| `client_id` | string | yes | Identifies the calling client; the request is rejected without it. |
| `client_sent_msg_ts` | int (ms since epoch) | **yes, in practice** | Time you send the request. Must be generated **at send time** — a stale value is rejected. See the warning below. |
| `client_timeout` | int (ms) | **yes, in practice** | How long the client is willing to wait. Omitting it is rejected. See the warning below. |
| `conversation_id` | int or `null` | no | Existing conversation to append to. `null` (or omitted) starts a new one. |
| `config_name` | string | no | Named configuration to answer under. |
| `is_refresh` | bool | no, but **needs a prior user turn** | Re-answer the previous turn instead of adding a new one. Not an independent switch — a refresh does not add your message to the conversation, so it needs an earlier turn to work from. If none survives (no `conversation_id` and no supplied history; a named conversation holding no turns; or a history of assistant turns only, which the refresh trim empties), the request is **rejected with `400`** ([`app.py:1692`][refreshguard]) and no conversation is created. |
| `provider` | string | stream only, **with `model`** | Override the LLM provider. Has no effect unless `model` is sent too — see [Overriding provider and model](#overriding-provider-and-model). Ignored entirely by `POST /api/get_chat_response`. |
| `model` | string | stream only, **with `provider`** | Override the model. Has no effect unless `provider` is sent too — see [Overriding provider and model](#overriding-provider-and-model). Ignored entirely by `POST /api/get_chat_response`. |
| `include_agent_steps` | bool | stream only | Include the incremental **answer text** — the `chunk` events ([`app.py:2418`][chunkgate]). Default `true`. Does **not** gate reasoning. Ignored by `POST /api/get_chat_response`. |
| `include_tool_steps` | bool | stream only | Include tool events (`tool_start`, `tool_output`, `tool_end`) **and reasoning events** (`thinking_start`, `thinking_end`, [`app.py:2398`][thinkgate]). Default `true`. Ignored by `POST /api/get_chat_response`. |

!!! warning "Send both timing fields, and generate the timestamp fresh"

    `client_sent_msg_ts` and `client_timeout` look optional and are not. Both default to
    `0` when absent ([`app.py:4660-4661`][parse]), and the timeout check is an unguarded
    comparison ([`app.py:1708`][check]):

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
      ([`app.py:2073`][streamerr]). A streaming client that checks only the HTTP status
      sees success. You must inspect the events.

    This is a bug in the handler, not the intended contract — the streaming loop applies
    the same check to the same variable but guards it, `if client_timeout and ...`
    ([`app.py:2154`][stream]), so `0` there means "no deadline" while here it means
    "deadline already passed". Tracked as
    [#175](https://github.com/fasrc/archi/issues/175); once fixed, both fields become
    genuinely optional and this warning goes away. Until then, this page documents what
    the endpoints actually do.

[parse]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L4660-L4661
[check]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L1708
[streamerr]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2073
[stream]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2154

**`last_message` is nested.** It is a list whose first element is the
`[sender, message]` pair — `[["User", "How do I submit a job?"]]`, **not**
`["User", "How do I submit a job?"]`. The handler reads `last_message[0]` and unpacks
it as `(sender, content)`.

Both endpoints validate the shape before the request proceeds. A well-formed value is a
non-empty list or tuple whose first element is itself a list or tuple of exactly two strings.
Every other value — a flat list, an empty list, `null`, a non-pair first element, or non-string
members — is rejected with **HTTP 400** and an error body naming the expected shape, e.g.:

```json
{"error": "last_message must be a list containing a [sender, message] pair of two strings, e.g. [[\"User\", \"hello\"]]"}
```

The check exists because the flat form `["AI", "hello"]` is a two-character string sequence,
and without validation `tuple("AI")` yields `sender="A"`, `content="I"` — a request that
returns HTTP 200 while silently discarding the caller's message. Both endpoints now reject it
before the pipeline is invoked, so no conversation row is created for the rejected request.

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

    Every chat route is registered through `require_auth` ([`app.py:2786`][authwrap]), so with
    `services.chat_app.auth.enabled: true` this command gets `401` — or a `302` to the login
    page when SSO is on and anonymous access is blocked — instead of an answer. Nothing about
    the request body is wrong in that case; it never reaches the handler.

    Against a deployment with **basic auth** enabled, log in first and reuse the session
    cookie (`/login` accepts a form-encoded `username` and `password`,
    [`app.py:3270`][loginform], and exists only when auth is enabled):

    ```bash
    curl -sS -c jar.txt -X POST http://localhost:7861/login \
      -d 'username=<user>&password=<password>'
    curl -sS -b jar.txt http://localhost:7861/api/get_chat_response \
      -H 'Content-Type: application/json' -d '<body as above>'
    ```

    With **SSO** the login is a browser redirect flow that curl cannot complete; copy the
    session cookie out of an already-logged-in browser session instead.

[authwrap]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2786
[loginform]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L3270

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
pipeline only under `if provider and model` ([`app.py:2090`][override]), so sending one
without the other is not a partial override — it is no override at all, and the request is
answered by the default pipeline. This is silent: there is no error and no warning, and the
answer looks normal, so a caller who sends `model` alone can receive a reply from a model
they did not ask for.

| Sent | Effect |
|---|---|
| `provider` + `model` | override **attempted** for this request only — may still fall back, see below |
| `provider` alone | **ignored**, default pipeline answers |
| `model` alone | **ignored**, default pipeline answers |

Sending both is necessary but not sufficient. **Treat the override as a request, not a
setting**: the only reliable way to know which model answered is to read it back off the
`final` event's **`model_used`** field ([`app.py:2591`][modelused]). Note that `final` carries
*two* model fields — `model` comes from the pipeline output's metadata, while `model_used` is
the request-local identity that reflects whether the override actually took. Comparing against
`model` will not tell you that. Everything below is why it matters.

The override is applied only if the LLM is constructed *and* a request-local pipeline view is
built from it, under a guard that also requires the active pipeline to expose an `agent_llm`
([`app.py:2109`][ovrguard]). Failures divide into two kinds — those that let the **default
pipeline** answer, and those that **end the stream with no answer at all** — and how you find
out differs again:

| Do you still get an answer? | How you find out | Examples (not exhaustive) |
|---|---|---|
| **No** — the stream ends | `{"type": "error", "status": 400}` | a construction-time `ValueError` — overrides disabled, or a provider name that does not resolve ([`app.py:2100`][ovrreject]) |
| **No** — the stream ends mid-answer | in-band `{"type": "error", "status": 500}` | a model string the provider builds happily and rejects on use — `get_chat_model` does not check the provider's catalogue, so an unknown model ID for OpenAI or OpenRouter surfaces at invocation, not at construction ([`app.py:2625`][outerr]) |
| **Yes** — from the default pipeline | `{"type": "warning", "message": "Using default model: …"}` | most construction failures, and a failed request-local pipeline build ([`app.py:2106`][ovrwarn], [`:2126`][ovrwarn2]) |
| **Yes** — from the default pipeline | **nothing at all**: no `error`, no `warning` | an active pipeline with no `agent_llm` ([`app.py:2109`][ovrguard]) |

So "the override failed" does **not** imply "the default answered" — the first two rows
terminate rather than fall back, and a client that assumes an answer is always coming will wait
for a `final` event that never arrives.

Two more consequences worth designing for. **A silent fallback is a normal-looking success** —
the answer arrives, nothing is flagged, and only `model_used` differs from what you asked for.
And **`400` is not the failure mode for a bad model ID**; a typo'd model name typically reaches
the provider and comes back as an in-band `500` partway through the stream.

So do not infer the answering model from your own request. Read `final.model_used`, and treat a
`warning` event as "my override did not take".

[ovrreject]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2100
[ovrwarn]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2106
[ovrwarn2]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2126
[ovrguard]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2109
[modelused]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2591
[outerr]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2625
[legacygate]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2439
[chunkyield]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2421
[evmeta]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L4811
[evtoolstart]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2352
[evtooloutput]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2365
[evtoolend]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2379
[evfinal]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2576
[everror]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2073
[traceusage]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2552
[chunkyield2]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2463
[traceevent]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2430
[stepemit]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L1755
[refreshguard]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L1692

[override]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2090

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
    [`app.py:4838`][streamopen] — not on the kind of error.

    **Before the stream opens — an ordinary HTTP status.** Check these as you would on any
    endpoint:

    | Failure | Status |
    |---|---|
    | Not authenticated, SSO on and anonymous access blocked | **302** redirect to login |
    | Not authenticated, otherwise | **401** `{"error": "Unauthorized"}` |
    | `client_id` missing ([`app.py:4794`][clientid]) | **400** `{"error": "client_id missing"}` |
    | Malformed `last_message` (not a nested pair of two strings) | **400** `{"error": "..."}` naming the expected shape |

    **After the stream opens — HTTP 200 plus an event.** The status line is already on the
    wire, so a failure inside the generator can only be reported in-band: the opening `meta`
    line, then

    ```json
    {"type": "error", "status": 408, "message": "..."}
    ```

    The timeout rejection described above is in this second group, and so is the `400` for a
    refresh with nothing to refresh ([`app.py:1692`][refreshguard]) — both are decided inside
    `_prepare_chat_context`, which runs after the response is constructed. **The same `400`
    arrives as a real HTTP status from `POST /api/get_chat_response`**, which is the clearest
    illustration of why this section exists: identical rejection, two different channels.

    Note that `400` appears in **both** groups on this endpoint, so the status alone does not
    tell you which one you are in: a malformed `last_message` is rejected in the route and
    arrives as a real HTTP `400`, while a refresh with nothing to refresh is decided inside the
    generator and arrives as an in-band event under HTTP `200`. What separates them is *where*
    the check runs, not the status it returns.

    A correct client therefore does **both**: check the HTTP status first, and — when it is
    `200` — still inspect the events for `type: "error"`, whose `status` field is the status of
    the **failed streaming operation**. Treating `200 OK` alone as success misses the second
    group; ignoring the status code misses the first.

    Read that `status` as this endpoint's own result, not as a prediction of what
    `POST /api/get_chat_response` would have done with the same body. Some failures exist only
    on this endpoint — an override rejected with an in-band `400` ([`app.py:2100`][ovrreject])
    has no counterpart there, because the non-streaming handler ignores `provider` and `model`
    altogether and would answer normally.

[streamopen]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L4838
[clientid]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L4794

Each line is a JSON object with a `type` field. Event types:

| Type | Description |
|------|-------------|
| Type | Gated by | Description |
|------|---|-------------|
| `meta` | — | Stream metadata, sent first; includes padding ([`app.py:4811`][evmeta]) |
| `chunk` | `include_agent_steps` | **The incremental answer text** — the event carrying the response as it is produced ([`app.py:2421`][chunkyield], [`:2463`][chunkyield2]) |
| `tool_start` | `include_tool_steps` | Agent is invoking a tool ([`:2352`][evtoolstart]) |
| `tool_output` | `include_tool_steps` | Tool result ([`:2365`][evtooloutput]) |
| `tool_end` | `include_tool_steps` | Tool invocation finished, with its completion status and duration ([`:2379`][evtoolend]) |
| `thinking_start` | `include_tool_steps` | Reasoning begins ([`:2392`][thinkgate]) |
| `thinking_end` | `include_tool_steps` | Reasoning ends ([`:2404`][thinkgate2]) |
| `step` | `include_tool_steps` | Legacy step event from a non-agent pipeline, carrying a `step_type` such as `tool_call` or `tool_result` ([`:1761`][stepemit]) |
| `final` | — | Final response with the full message and metadata ([`:2576`][evfinal]) |
| `warning` | — | The request continued, but not as asked — e.g. an override fell back to the default model ([`:2106`][ovrwarn]) |
| `error` | — | A failure, carrying its own `status` ([`:2073`][everror]) |

That table is **derived from the handler rather than maintained by hand** — it is every
`"type"` the streaming generator yields. To re-derive it after a change, list the yielded
dict literals and exclude the ones appended to `trace_events`:

```bash
grep -n '"type":' src/interfaces/chat_app/app.py
```

A match reaches the wire only if its dict is `yield`ed. Two cases the grep does not settle on
its own: a dict appended to `trace_events` is trace-only (`text`, `usage`), and a dict bound to
a variable first — `start_event = {...}` … `yield start_event` for `tool_start` — is on the
wire despite no `yield` on the matching line. Check the enclosing statement for each hit.

Even so, **ignore unknown `type` values rather than failing on them** — a pipeline may emit
something not listed here — while still handling `chunk`, which is where the answer arrives.

!!! note "`text` and `usage` are trace events, not stream events"

    Two types are recorded in the request trace and **never** emitted on the NDJSON stream, so
    you read them back through `GET /api/trace/<trace_id>` rather than by parsing the response:

    - `text` — the pipeline's *internal* output type. The dispatch converts it into the `chunk`
      event on the wire ([`app.py:2415-2421`][chunkyield]) and records `text` separately in the
      trace ([`:2430`][traceevent]).
    - `usage` — token accounting ([`:2552`][traceusage]).

    Earlier revisions of this table listed `text` and omitted `chunk`, which is the wrong way
    round for anyone parsing the stream. If you are matching the handler's
    `elif event_type == "text"` branch against this table, note that the branch names the
    *input* it consumes, not the event it emits.

!!! warning "The two step flags do not group events the way their names suggest"

    Read this before using either flag to filter the stream. The grouping is by
    **flag**, not by the sense of the flag's name:

    | Flag | Actually gates |
    |---|---|
    | `include_agent_steps` | the incremental answer text — `chunk` events ([`app.py:2418`][chunkgate], [`:2452`][chunkgate2]) |
    | `include_tool_steps` | tool activity (`tool_start`, `tool_output`, `tool_end`), reasoning (`thinking_start` / `thinking_end`, [`app.py:2398`][thinkgate], [`:2412`][thinkgate2]), **and** the legacy `step` events that non-agent pipelines emit ([`app.py:2439`][legacygate] → [`:1755`][stepemit]) |

    Those are the event types the streaming dispatch recognizes by name. Anything else falls
    through to legacy conversion ([`app.py:2439`][legacygate]), where what reaches you depends
    on the *shape* of the underlying message rather than on the category you would expect —
    that path is entered with `include_agent_steps=False`, and answer content in it is gated by
    `include_agent_steps` further down ([`:2452`][chunkgate2]).

    So do not treat either flag as a suppression guarantee for an event type not listed above.
    **If there is content you must not surface, filter on what you actually receive** rather
    than relying on a flag to have withheld it.

    So reasoning events are controlled by the **tool** flag. Setting
    `include_agent_steps: false` to suppress reasoning does the opposite of what you
    want twice over: the `thinking_*` events still arrive, and you silently stop
    receiving streamed answer text. The `final` event still carries the complete
    answer, so the loss shows up as an answer that appears all at once at the end
    rather than as an error.

    To suppress reasoning, set `include_tool_steps: false` — accepting that tool
    events go with it. The two are not separable through this API.

[chunkgate]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2418
[chunkgate2]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2452
[thinkgate]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2398
[thinkgate2]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L2412

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
