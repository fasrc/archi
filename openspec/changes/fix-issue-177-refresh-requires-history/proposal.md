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

- Reject the unsatisfiable combination with **HTTP 400** before any conversation row is created.
  The condition is the absence of *any* source of prior turns — `is_refresh and conversation_id is
  None and external_history is None` — not the absence of a `conversation_id`.
- Give `400` a caller-appropriate message on both endpoints. Today the two error-message chains
  (`app.py:2019-2025` streaming, `:4668-4674` non-streaming) know only 408 and 403 and fall through
  to "server error; see chat logs for message", which would misreport a client error as a server
  fault.
- Collapse those two duplicated chains into one shared helper so a future status cannot be added to
  one endpoint and forgotten on the other.
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
- **Behaviour change:** yes — a request that previously returned `200` now returns `400`. That is the
  point of the change, and it is a request that could not have been producing a useful answer.
