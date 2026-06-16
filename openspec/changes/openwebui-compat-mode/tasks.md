## 1. Inline Citation Formatter

- [x] 1.1 Create `src/archi/utils/citation_formatter.py` with `format_citations(source_documents, scores) -> str` — deduplicate by filename, keep highest score per doc, return markdown block (`---\n**Sources:**\n- ...`). Reference existing logic in `ChatWrapper.get_top_sources()` (app.py:447).
- [x] 1.2 Add collection label support: when sources span multiple collections, include `[collection_name]` after filename. Omit labels when all sources are from one collection.
- [x] 1.3 Write unit tests for citation formatter: no sources (empty string), single source, duplicate chunks, multi-collection labeling, score formatting.

## 2. Bearer Token Auth

- [x] 2.1 Add `api_token_hash VARCHAR(64)` column to `users` table in `src/cli/templates/init.sql`. Add index on `api_token_hash`.
- [x] 2.2 Add token generation to `UserService` (`src/utils/user_service.py`): `generate_api_token(user_id) -> str` — generates `archi_<32-hex-chars>`, stores SHA-256 hash in `api_token_hash`, returns plaintext once.
- [x] 2.3 Add token lookup to `UserService`: `get_user_by_api_token(token) -> Optional[User]` — SHA-256 hashes the input, queries `users` by `api_token_hash`.
- [x] 2.4 Add token revocation to `UserService`: `revoke_api_token(user_id) -> bool` — sets `api_token_hash` to NULL.
- [x] 2.5 Write unit tests for token generation, lookup (valid/invalid/revoked), and hash storage.

## 3. OpenAI-Compatible Blueprint

- [x] 3.1 Create `src/interfaces/chat_app/openai_compat.py` with Flask Blueprint registered at `/v1`. Define `register_openai_compat(app, chat_wrapper)` function following the pattern in `api.py:1083`.
- [x] 3.2 Implement `GET /v1/models` — read available config names from `chat_wrapper`, return `{"object": "list", "data": [{"id": name, "object": "model", "created": <boot_timestamp>, "owned_by": "archi"}, ...]}`.
- [x] 3.3 Implement bearer token auth middleware for `/v1` routes: extract `Authorization: Bearer <token>`, call `UserService.get_user_by_api_token()`, enforce `chat:query` RBAC permission. Return OpenAI-format errors (401/403). Skip when auth disabled.
- [x] 3.4 Implement `POST /v1/chat/completions` request parsing: validate `model`, `messages`, extract `temperature`/`max_tokens` overrides. Map `model` → `config_name`, `messages[-1].content` → query, `messages[:-1]` → history. Return 404 for unknown model.
- [x] 3.5 Implement streaming response path: call `ChatWrapper.stream()`, wrap with SSE translator generator. Set `Content-Type: text/event-stream`. Translate `chunk` → delta content, `final` → citation block + finish_reason stop + `[DONE]`. Drop `tool_start`/`thinking_start` events.
- [x] 3.6 Implement non-streaming response path: accumulate from `ChatWrapper.stream()` (not `__call__()` — stream path has tracing, tool tracking, usage capture that __call__ lacks). Buffer chunks, join on completion, return complete JSON response with `choices[0].message.content` and `finish_reason: "stop"`.
- [x] 3.7 Implement error handling: pipeline exceptions → 500, unknown model → 404, auth failures → 401/403. All errors in `{"error": {"message": "...", "type": "..."}}` format. Mid-stream errors emit `[Error: ...]` content chunk.

## 4. Conversation Persistence

- [x] 4.1 Add `external_chat_id VARCHAR(200)` column to `conversation_metadata` in `src/cli/templates/init.sql`. Add unique index on `(external_chat_id)` for fast lookups.
- [x] 4.2 Add `get_or_create_conversation_by_external_id(external_chat_id, user_id, client_id, title) -> int` to `ChatWrapper` — looks up `conversation_metadata` by `external_chat_id`, creates if not found, returns `conversation_id`.
- [x] 4.3 In the `/v1/chat/completions` handler, read `X-OpenWebUI-Chat-Id` header. If present, call `get_or_create_conversation_by_external_id()` to map to archi conversation. If absent, create a new conversation per request (fallback for non-Open WebUI clients).
- [x] 4.4 After streaming completes, persist the user message and accumulated assistant response via `ChatWrapper.insert_conversation()`. On mid-stream error, persist user message only.
- [x] 4.5 Write integration test: send 3 `/v1/chat/completions` requests with the same `X-OpenWebUI-Chat-Id` header, verify all 3 map to one archi conversation with 6 messages (3 user + 3 assistant). Send a 4th request with a different chat ID, verify it creates a second conversation.

## 5. Feature Flag and Registration

- [x] 5.1 Add `services.chat_app.openai_compat.enabled` config key (default: `false`) to `src/cli/templates/base-config.yaml`.
- [x] 5.2 In `FlaskAppWrapper.__init__` (app.py:2112), conditionally call `register_openai_compat(self.app, self.chat)` when the feature flag is enabled. Pass the `ChatWrapper` instance.
- [x] 5.3 Add token management endpoints to the native API (`api.py`): `POST /api/users/me/api-token` (generate), `DELETE /api/users/me/api-token` (revoke), `GET /api/users/me/api-token` (check if exists, don't return plaintext).

## 6. Documentation

## 7. Verification Fixes

- [x] 7.1 CRITICAL: Fix citation bug — `final` event `response` is a string, not `PipelineOutput`. Add `source_documents` and `retriever_scores` to the `final` event dict in `ChatWrapper.stream()`, read them in the blueprint.
- [x] 7.2 CRITICAL: Fix DB connection leak in `_get_or_create_conversation()` and `_persist_messages()` — add `conn.close()` in finally blocks.
- [x] 7.3 WARNING: `temperature`/`max_tokens` extracted but never passed to `stream_kwargs`. Document as silently ignored per spec.
- [x] 7.4 WARNING: `check_api_token` endpoint uses `factory._get_connection()` (private). Add `has_api_token(user_id)` to `UserService` and use it.
- [x] 7.5 Write integration test for conversation persistence (task 4.5).
- [x] 7.6 SUGGESTION: Update `v1-bearer-auth` spec wording from "stored encrypted" to "stored as a one-way hash".

## 6. Documentation

- [x] 6.1 Write `docs/docs/openwebui-integration.md`: setup guide covering Docker Compose config, `OPENAI_API_BASE_URL`, `BYPASS_EMBEDDING_AND_RETRIEVAL`, model registration in Open WebUI, group-based access control.
- [x] 6.2 Add `examples/deployments/openwebui/config.yaml` and Docker Compose example showing archi + Open WebUI + vLLM deployment.
- [x] 6.3 Write `/v1` API reference in `docs/docs/api-reference-v1.md`: endpoints, request/response schemas, authentication, error format.
