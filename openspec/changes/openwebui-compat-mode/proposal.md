## Why

archi has no OpenAI-compatible API. Clients must use archi's native `/api/get_chat_response_stream` endpoint with its custom NDJSON format. This locks archi into its own frontend and prevents integration with [Open WebUI](https://github.com/open-webui/open-webui), LiteLLM, Continue.dev, or any other OpenAI-compatible client. Adding a `/v1` API layer lets archi serve as a backend for standard frontends while keeping all pipelines, agents, and data ingestion intact.

## What Changes

- Add `GET /v1/models` endpoint returning archi configs/collections as OpenAI model objects (`id`, `object`, `created`, `owned_by`)
- Add `POST /v1/chat/completions` endpoint accepting OpenAI chat requests and returning SSE-formatted streaming responses (`Content-Type: text/event-stream`)
- Build a translator that converts archi's `PipelineOutput` NDJSON events (`chunk`, `tool_start`, `thinking_start`, `final`) to OpenAI SSE delta format (`data: {"choices": [{"delta": {"content": "..."}}]}`)
- Map OpenAI request parameters (`model` → `config_name`, `temperature`, `max_tokens`) to archi pipeline kwargs
- Authenticate `/v1` requests via `Authorization: Bearer <token>` headers, resolving tokens to archi users for RBAC checks
- Persist every `/v1` conversation in archi's PostgreSQL via `ConversationService` (independent of any frontend's own history)
- Build a shared inline citation formatter that appends source documents to response text (used by both `/v1` and native UI)

## Capabilities

### New Capabilities

- `openai-compat-api`: The `/v1/models` and `/v1/chat/completions` endpoints, including SSE streaming, request/response translation, and OpenAI parameter mapping
- `v1-bearer-auth`: Bearer token authentication on `/v1` endpoints, resolving tokens to archi users and enforcing RBAC permissions
- `v1-conversation-persistence`: Conversation creation and message persistence via `ConversationService` for all `/v1` requests
- `inline-citations`: Shared formatter that appends deduplicated source document citations to response text, used across all response paths

### Modified Capabilities

_(none — no existing specs are affected)_

## Impact

- **New code**: Flask blueprint for `/v1` routes (~200-300 lines), SSE stream adapter module, OpenAI response translator module, citation formatter utility
- **Existing code**: No modifications to existing endpoints or pipelines. The `/v1` blueprint registers alongside existing routes in `src/interfaces/chat_app/`
- **Dependencies**: No new third-party dependencies. Uses existing Flask, archi pipeline, and ConversationService infrastructure
- **APIs**: New public API surface (`/v1/models`, `/v1/chat/completions`). archi's native API unchanged
- **Deployment**: Open WebUI connects via `OPENAI_API_BASE_URL=http://chatbot:7861/v1`. Requires `BYPASS_EMBEDDING_AND_RETRIEVAL=true` in Open WebUI to disable its built-in RAG
- **Documentation**: New Open WebUI integration guide, example Docker Compose, `/v1` API reference
