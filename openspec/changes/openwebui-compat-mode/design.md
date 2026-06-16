## Context

archi's chat interface is a Flask app (`FlaskAppWrapper` in `src/interfaces/chat_app/app.py`) that exposes a native REST API with NDJSON streaming. The core streaming path is `ChatWrapper.stream()` which yields Python dicts that get JSON-serialized into newline-delimited events. There is no OpenAI-compatible endpoint — clients must understand archi's custom event format.

The existing infrastructure provides:
- `ChatWrapper.stream()` — yields event dicts (`chunk`, `tool_start`, `thinking_start`, `final`, `error`)
- `ChatWrapper.__call__()` — synchronous (non-streaming) invocation returning a complete response
- `config_name` routing — resolves which agent/pipeline handles a request
- `ConversationService` — creates and persists conversations in PostgreSQL
- RBAC system — `@require_perm(Permission.Chat.QUERY)` decorators, role registry
- Flask blueprint pattern — `api.py` blueprint registered alongside main app routes

Open WebUI (verified against source) connects via:
- `OPENAI_API_BASE_URL` pointing at the backend
- `GET /v1/models` to discover available models (needs `id`, `object`, `created`, `owned_by`)
- `POST /v1/chat/completions` with streaming detected by `Content-Type: text/event-stream` header
- SSE stream passed through without parsing — raw bytes forwarded to the browser
- `Authorization: Bearer <key>` header forwarded to backend from `OPENAI_API_KEYS` config

## Goals / Non-Goals

**Goals:**
- Expose archi's RAG pipelines via standard OpenAI Chat Completions API
- Support both streaming (`text/event-stream`) and non-streaming (`application/json`) responses
- Authenticate via bearer tokens, enforcing existing RBAC permissions
- Persist all `/v1` conversations in archi's database for debugging and evaluation
- Standardize inline source citations across all response paths
- Zero modification to existing native API routes or pipeline code

**Non-Goals:**
- Embeddings API (`/v1/embeddings`) — not needed for chat frontends
- Completions API (`/v1/completions`) — legacy, not used by Open WebUI
- File upload via `/v1` — archi's data-manager handles ingestion separately
- WebSocket support — SSE is sufficient and matches OpenAI's protocol
- Replacing archi's native UI — `/v1` is additive, native UI unchanged
- Multi-collection routing — separate proposal, works as enhancement when present

## Decisions

### 1. Flask blueprint in a new file, not modifications to app.py

**Decision:** Create `src/interfaces/chat_app/openai_compat.py` as a Flask Blueprint registered at `/v1`.

**Rationale:** `app.py` is already ~5500 lines. Adding `/v1` routes there would increase complexity. A separate blueprint:
- Isolates all OpenAI-compat code in one file
- Can be conditionally registered (feature flag)
- Avoids merge conflicts with ongoing work on the native API
- Follows the pattern of `api.py` which is already a separate blueprint

**Alternative considered:** Adding routes directly to `FlaskAppWrapper`. Rejected because it couples the compat layer to the main app class and makes it harder to disable.

### 2. Call ChatWrapper.stream() directly, not the HTTP endpoint

**Decision:** The `/v1` blueprint calls `ChatWrapper.stream()` (the Python method) directly, not archi's HTTP streaming endpoint.

**Rationale:** Calling the HTTP endpoint would mean HTTP-in-HTTP (Open WebUI → Flask `/v1` → Flask `/api/get_chat_response_stream`). Instead, the `/v1` handler gets a reference to the same `ChatWrapper` instance and calls its `.stream()` method, iterating over the same event dicts the native endpoint uses. This:
- Avoids unnecessary serialization/deserialization round-trip
- Shares the exact same pipeline execution path
- Accesses the same conversation persistence and trace recording

**Alternative considered:** HTTP proxy to native endpoint. Rejected due to latency, complexity, and the fact that both endpoints run in the same Flask process.

### 3. Generator-based SSE translation (no intermediate buffer)

**Decision:** The SSE translator is a Python generator that wraps `ChatWrapper.stream()`, translating each event dict to an SSE line as it's yielded.

**Rationale:** This preserves streaming semantics — tokens appear in the client as they're generated, with no buffering. The pattern:

```python
def _translate_to_sse(chat_wrapper, **kwargs):
    for event in chat_wrapper.stream(**kwargs):
        sse_line = _event_to_sse(event)
        if sse_line:
            yield sse_line
    yield "data: [DONE]\n\n"
```

Each archi event maps to zero or one SSE lines:
- `chunk` → `data: {"choices": [{"delta": {"content": "..."}}]}\n\n`
- `final` → citation chunk (if sources) + `data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n`
- `tool_start`, `tool_end`, `thinking_start`, `thinking_end` → dropped (no SSE equivalent)
- `error` → `data: {"choices": [{"delta": {"content": "[Error: ...]"}}]}\n\n`

### 4. API tokens stored as hashed values in a new column

**Decision:** Add an `api_token_hash` column to the `users` table. Tokens are generated as `archi_<random_hex>`, hashed with SHA-256 before storage. Lookup is by hash.

**Rationale:**
- Tokens are long-lived (unlike session cookies), so storing plaintext is a security risk
- SHA-256 hash allows O(1) lookup without exposing the token if the DB is compromised
- Prefix `archi_` makes tokens identifiable and distinguishable from other credentials
- Single token per user keeps the model simple; multi-token can be added later with a separate table

**Alternative considered:** Separate `api_tokens` table with multiple tokens per user, scopes, and expiry. Rejected as overengineering for the initial implementation — can be added as an enhancement.

### 5. Conversation tracking via request-scoped state

**Decision:** Each `/v1/chat/completions` request creates a new conversation (or could optionally resume one via a custom header). The conversation ID is not exposed to the client.

**Rationale:** The OpenAI protocol has no concept of conversation IDs — each request is stateless, with full history in `messages`. archi creates conversations internally for auditing. Since Open WebUI sends the full message history each time, archi doesn't need to resume conversations, but storing each exchange allows:
- Debugging individual requests
- Evaluating response quality
- A/B testing analysis via archi's native tools

**Alternative considered:** Mapping Open WebUI's conversation ID (if passed in headers) to archi's conversation ID. Deferred — adds coupling between the two systems.

### 6. Citation formatter as a standalone function in src/archi/utils/

**Decision:** Place the citation formatter at `src/archi/utils/citation_formatter.py` as a pure function: `format_citations(source_documents: List[Document], scores: List[float]) -> str`.

**Rationale:**
- Pure function with no dependencies on Flask, streaming, or pipeline internals
- Usable by `/v1` translator (appends to final SSE content), native endpoint (appends to NDJSON final), and any future interface
- Located in `src/archi/utils/` alongside other cross-cutting utilities
- Returns a markdown string — the caller decides how to deliver it

### 7. Feature flag to enable/disable /v1

**Decision:** The `/v1` blueprint registration is gated by a config flag `services.chat_app.openai_compat.enabled` (default: `false`).

**Rationale:** The `/v1` endpoint is a new public API surface. Gating it behind a flag allows:
- Existing deployments to upgrade without exposing a new endpoint
- Operators to enable it explicitly when setting up Open WebUI integration
- Disabling it if security concerns arise without a code change

## Risks / Trade-offs

**[Token security]** → Bearer tokens are long-lived and could be leaked. Mitigation: tokens are hashed in DB, prefixed for identification, and can be revoked. Future enhancement: token expiry and rotation.

**[Conversation bloat]** → Every `/v1` request creates a new conversation since OpenAI protocol is stateless. A user's 50-message Open WebUI chat becomes 50 separate archi conversations. → Mitigation: conversations are lightweight (metadata + messages). Can add a cleanup job or conversation grouping later.

**[Event translation fidelity]** → Dropping `tool_start`/`thinking_start` events means the `/v1` user doesn't see agent reasoning. → Accepted trade-off: OpenAI protocol has no equivalent. Users who need step visibility use archi's native UI.

**[SSE error mid-stream]** → If the pipeline errors after streaming has started, the HTTP status is already 200. → Mitigation: emit an error content chunk `[Error: ...]` in the SSE stream, matching how OpenAI handles mid-stream errors. The conversation persists the user message but not the partial response.

**[Open WebUI model registration]** → Models from `/v1/models` must be registered in Open WebUI's DB before group-based access control works for non-admin users. → Mitigation: document as a setup step. Admin registers each archi config as a model in Open WebUI's settings.

## Resolved Questions

1. **Should the `/v1` endpoint support resuming archi conversations?** → **Yes — via Open WebUI's `X-OpenWebUI-Chat-Id` header.** Open WebUI already forwards conversation IDs when `ENABLE_FORWARD_USER_INFO_HEADERS=True`. archi reads this header, maps it to an internal conversation (creating on first sight, resuming thereafter), and stores the mapping in a new `external_chat_id` column on `conversation_metadata`. A 50-message chat maps to one archi conversation. Clients without the header get one conversation per request as fallback.

2. **Should non-streaming responses use `ChatWrapper.__call__()` or accumulate from `.stream()`?** → **Accumulate from `.stream()`.** The streaming path has ~400 lines of instrumentation that `__call__()` lacks: agent tracing, tool call tracking, provider/model override, client timeout enforcement, cancellation handling, and usage capture. Using `__call__()` would make non-streaming `/v1` requests invisible in archi's trace/debug tools. Accumulating from `.stream()` is ~5 extra lines and guarantees identical instrumentation.
