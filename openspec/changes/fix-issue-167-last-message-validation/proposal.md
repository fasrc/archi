## Why

Both chat endpoints read the request field `last_message` with no shape validation at all:
`_prepare_chat_context` does `sender, content = tuple(message[0])` (`app.py:1633`). When a
caller sends the **flat** shape instead of the canonical nested one, that unpack consumes
the *characters of the sender string* rather than a pair. A three-plus-character sender
(`["User", "hello"]`) raises and surfaces as HTTP 500; a **two-character** sender
(`["AI", "hello"]`) unpacks cleanly into `sender="A"`, `content="I"` and the request
**succeeds against the wrong content** — the caller's actual message is discarded, the model
answers the single character `"I"`, and nothing in the logs marks the request as malformed.

The silent case is the one that matters: it is indistinguishable from success. It was
surfaced by Codex review on #159 and deliberately deferred there because #159 was
docs-only and this is a behaviour change ([#167](https://github.com/fasrc/archi/issues/167)).

## What Changes

- **New pure helper module** `src/interfaces/chat_app/request_validation.py` that validates
  the `last_message` shape and returns the `(sender, content)` pair, raising a typed error
  naming the expected shape. It is import-light and has no Flask dependency, so it is unit
  tested directly.
- **Both chat endpoints reject a malformed `last_message` with HTTP 400** — a real HTTP
  status, before any work is done: `POST /api/get_chat_response` (`app.py:4620`) and
  `POST /api/get_chat_response_stream` (`app.py:4711`). On the streaming endpoint the
  rejection is returned *before* the generator is constructed, so it arrives as an ordinary
  `400` with no `meta` line and no in-band error event — consistent with how the existing
  missing-`client_id` check already behaves there (`app.py:4730`).
- **Accepted**: a non-empty list/tuple whose first element is itself a non-string sequence
  of exactly two items, both strings. Everything else — `[]`, `null`, a missing field, a
  flat pair, a first element that is not a 2-item pair, non-string members — is a 400.
- **`docs/docs/api_reference.md` is corrected.** The page currently documents the
  no-validation behaviour ("The endpoint does not currently validate the shape", plus a
  paragraph on the 500/silent-wrong-answer outcomes). That description becomes wrong the
  moment this lands.
- Not a breaking change for in-repo clients: `static/chat.js:266` and `openai_compat.py:242`
  already send the canonical nested shape.

## Capabilities

### New Capabilities
- `chat-request-validation`: how the chat endpoints validate the request payload shape
  before processing it, and what they return when it is malformed.

### Modified Capabilities
<!-- None. The related capability `chat-api-request-contract` (added by the pending change
     fix-issue-138-chat-docstring-payload-shape) documents the canonical shape and explicitly
     scopes enforcement out; it is not yet in openspec/specs/, so this change adds a separate
     enforcement capability rather than a delta against an unarchived spec. -->

## Impact

- **Code**: new `src/interfaces/chat_app/request_validation.py`; small edits at two call
  sites in `src/interfaces/chat_app/app.py` (`get_chat_response`,
  `get_chat_response_stream`).
- **API**: `POST /api/get_chat_response` and `POST /api/get_chat_response_stream` gain a
  `400` response for a malformed `last_message`. Requests that previously returned 500 now
  return 400; requests that previously returned 200-with-wrong-content now return 400.
  This is the intended behaviour change.
- **Docs**: `docs/docs/api_reference.md` request-body table and the "`last_message` is
  nested" section.
- **Tests**: new unit tests for the helper, plus endpoint-level tests that drive the two
  handlers in a Flask request context so the new call-site lines are covered (the request
  path has no test coverage today).
- **Dependencies**: none added.
