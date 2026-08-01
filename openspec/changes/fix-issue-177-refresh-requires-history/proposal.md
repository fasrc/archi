## Why

`is_refresh: true` sent with no `conversation_id` and no `external_history` causes the chat
handler to invoke the pipeline with **no user turn at all**.

`_prepare_chat_context` (`src/interfaces/chat_app/app.py:1618`) opens a new conversation with
`history = []` (`:1639-1641`), then skips appending the caller's message because the request is a
refresh (`:1657-1658`). The refresh trim at `:1650-1652` is a no-op on the empty history. The model
is therefore asked to answer nothing, and the caller receives a plausible-looking response to a
message the server discarded. There is no error and no warning.

A second, quieter effect: `:1640` has already created a conversation row by then, so every such
request leaves an empty conversation behind.

`is_refresh` means "re-answer the previous turn instead of adding a new one". Both halves of that
require prior turns to exist. When none do, the request is not satisfiable, and answering an empty
prompt is the worst available response — it is indistinguishable from success.

## What Changes

- Reject a refresh for which **no prior turn survives history resolution and the refresh trim**,
  before any conversation row is created or timestamp updated. The test is on the resolved history,
  not on which fields were supplied: a field-presence predicate is a proxy that admits
  `external_history=[]`, an assistant-only history, and a `conversation_id` naming an empty
  conversation.
- Report it on **each endpoint's own error channel**, which differ: `POST /api/get_chat_response`
  returns HTTP `400`; `POST /api/get_chat_response_stream` returns HTTP **200** with an in-band
  `{"type": "error", "status": 400}`, because its response is constructed before this check runs.
- Give that status a caller-appropriate message. Today the two error-message chains
  (`app.py:2019-2025` streaming, `:4668-4674` non-streaming) know only 408 and 403 and fall through
  to "server error; see chat logs for message", which would misreport a client error as a server
  fault.
- Collapse those chains into one shared helper so a future status cannot be added to one endpoint
  and forgotten on the other — including the streaming exception branches, which carried their own
  hard-coded copies of the 403 and 500 text.
- Document the behaviour in `docs/docs/api_reference.md`, replacing the current `is_refresh` row
  that describes the broken behaviour.

**Explicitly not changed:** a refresh that *does* have a source of prior turns. That includes a
refresh carrying `external_history` with no `conversation_id`, which is coherent — the supplied
history is trimmed of trailing assistant turns and re-answered. An earlier framing of this fix
would have rejected it; see `design.md`.

## Capabilities

### New Capabilities

- `chat-refresh-semantics`: when the chat endpoints honour `is_refresh`, and what they do when the
  request supplies nothing to refresh.

## Impact

- **Affected code:** `src/interfaces/chat_app/app.py` — `_prepare_chat_context` (guard), and the two
  error-message chains at the streaming call site and the non-streaming route.
- **Affected docs:** `docs/docs/api_reference.md`, the `is_refresh` request-body row.
- **Callers:** no in-repo client sends the rejected combination. `openai_compat.py:272` always sends
  `is_refresh: False`; the web UI (`static/script.js:781`, `:826`) sends `is_refresh` alongside a
  `conversation_id`. External API callers who send it today receive an answer to an empty prompt, so
  the change replaces a silent wrong answer with an explicit rejection.
- **Behaviour change:** yes, and it differs by endpoint. `POST /api/get_chat_response` now returns
  HTTP `400` where it previously returned `200` with an answer to an empty prompt.
  `POST /api/get_chat_response_stream` still returns HTTP **200** — its response is constructed
  before this check runs — but the body now carries an in-band `{"type": "error", "status": 400}`
  event instead of an answer. In both cases the request could not have been producing a useful
  result, so what changes is that the failure is now visible.
