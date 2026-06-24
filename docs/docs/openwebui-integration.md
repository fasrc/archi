# Open WebUI Integration

archi can serve as an OpenAI-compatible backend for [Open WebUI](https://github.com/open-webui/open-webui), exposing its RAG pipelines via `/v1/models` and `/v1/chat/completions` endpoints.

## Prerequisites

- A running archi deployment (Docker/Podman)
- Open WebUI instance

## Setup

### 1. Enable the OpenAI-compatible API

In your archi `config.yaml`:

```yaml
services:
  chat_app:
    openai_compat:
      enabled: true
```

Redeploy to apply: `archi deploy <name>`.

### 2. Generate an API token

If authentication is enabled, generate a token via archi's API:

```bash
curl -X POST http://localhost:7861/api/users/me/api-token \
  -H "Cookie: session=<your-session-cookie>"
```

Save the returned `archi_...` token. It's shown once and cannot be retrieved later.

### 3. Configure Open WebUI

Set these environment variables in your Open WebUI deployment:

```yaml
environment:
  OPENAI_API_BASE_URL: "http://chatbot:7861/v1"
  OPENAI_API_KEYS: "archi_<your-token>"
  BYPASS_EMBEDDING_AND_RETRIEVAL: "true"
  ENABLE_FORWARD_USER_INFO_HEADERS: "true"
```

Key settings:

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_BASE_URL` | Points Open WebUI at archi's `/v1` endpoint |
| `OPENAI_API_KEYS` | Bearer token for authentication |
| `BYPASS_EMBEDDING_AND_RETRIEVAL` | Disables Open WebUI's built-in RAG (archi handles retrieval) |
| `ENABLE_FORWARD_USER_INFO_HEADERS` | Forwards `X-OpenWebUI-Chat-Id` header for conversation mapping |

### 4. Register models in Open WebUI

After starting Open WebUI, archi's config will appear in the model dropdown. For group-based access control:

1. Go to **Admin Panel > Models** in Open WebUI
2. Register each archi config as a model
3. Assign group permissions as needed

## Docker Compose Example

See `examples/deployments/openwebui/` for a complete example deployment with archi + Open WebUI + vLLM.

## Architecture

```
                          +-----------------+
                          |   Open WebUI    |
                          |   (Frontend)    |
                          +--------+--------+
                                   |
                     Authorization: Bearer archi_...
                     X-OpenWebUI-Chat-Id: abc-123
                                   |
                          +--------v--------+
                          |  archi /v1 API  |
                          |  (Blueprint)    |
                          +--------+--------+
                                   |
                    +--------------+--------------+
                    |              |               |
              ChatWrapper    ConversationService  UserService
              .stream()      (persistence)        (auth)
                    |
              RAG Pipeline
              (retrieval + LLM)
```

## Conversation Tracking

When `ENABLE_FORWARD_USER_INFO_HEADERS` is enabled, Open WebUI sends its conversation ID in the `X-OpenWebUI-Chat-Id` header. archi maps this to an internal conversation, so a multi-turn chat in Open WebUI stays as one conversation in archi's database.

Without the header (e.g., using curl), each request creates a separate conversation.

## What You Lose

The `/v1` endpoint provides the core chat experience but omits some archi-native features:

- **Agent reasoning steps** — tool calls and thinking events are not visible via the OpenAI protocol
- **A/B testing** — requires archi's native UI
- **Document selection** — per-conversation document overrides are not available

Users who need these features should use archi's native chat interface.

## Token Management

| Action | Endpoint |
|--------|----------|
| Generate token | `POST /api/users/me/api-token` |
| Check if token exists | `GET /api/users/me/api-token` |
| Revoke token | `DELETE /api/users/me/api-token` |

Tokens are prefixed with `archi_` and stored as SHA-256 hashes. Revoking a token invalidates it immediately.

## Troubleshooting

**Models not appearing in Open WebUI**
- Verify `OPENAI_API_BASE_URL` points to archi's host and port
- Check that `openai_compat.enabled: true` is set in config
- Test directly: `curl http://localhost:7861/v1/models -H "Authorization: Bearer archi_..."`

**401 Unauthorized**
- Ensure the token is valid and hasn't been revoked
- If auth is disabled on archi, no token is needed

**Responses don't include sources**
- Sources appear as a markdown block at the end of responses
- If the pipeline returns no source documents, no citation block is appended
