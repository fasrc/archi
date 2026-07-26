## Why

The `get_chat_response` docstring in `src/interfaces/chat_app/app.py` documents a
`last_message` payload shape the endpoint does not accept. It describes a flat two-element
list (`["User", "hello"]`), but the handler unpacks the **first element** of `last_message`
as a `(sender, content)` pair (`_prepare_chat_context`, `app.py:1633`:
`sender, content = tuple(message[0])`). A client built from the docstring sends the flat
shape, `message[0]` becomes the string `"User"`, `tuple("User")` yields four characters, and
the route raises `ValueError: too many values to unpack (expected 2)` → HTTP 500. This was
reproduced live on 2026-07-22 against a dev deployment at `a8e06c79`: all five queries built
from the docstring returned 500; the same queries with `[["User", ...]]` succeeded. The
docstring is the only integrator-facing description of this endpoint's contract, so a wrong
docstring is a direct correctness bug for anyone integrating against it.

## What Changes

- Rewrite the `last_message` payload description in the `get_chat_response` docstring
  (`src/interfaces/chat_app/app.py`, around `:4620-4634`) to document the shape the code
  actually accepts: `last_message` is a **list containing a single `[sender, message]`
  pair** (e.g. `[["User", "How do I submit a job?"]]`), and only the first pair is read.
- No executable-code change. This is a documentation-accuracy fix only.
- **Out of scope (explicitly deferred):** changing the handler to validate the payload and
  return HTTP 400 instead of an unhandled 500. That is a behavior change in a
  coverage-trapped file and must ship as a separate PR routing validation through a tested
  helper module.

## Capabilities

### New Capabilities
- `chat-api-request-contract`: The documented request contract of the chat-app HTTP
  endpoint `get_chat_response`, specifically the shape of the `last_message` field, kept in
  agreement with the shape the handler actually consumes.

### Modified Capabilities
<!-- None: no existing spec covers the chat-app HTTP request contract, and no runtime
     behavior changes. -->

## Impact

- **Code:** `src/interfaces/chat_app/app.py` — docstring/comment lines only, no executable
  change.
- **Consumers:** integrators who build requests to `/api/get_chat_response` from the
  docstring; in-repo clients (`static/chat.js:266`, `openai_compat.py:242`) already send the
  correct nested shape and are unaffected.
- **Coverage/gate:** `app.py` is not imported by unit tests, so a docstring-only edit adds no
  executable lines and does not trip the ≥80% diff-cover gate. Any executable change here
  would fail diff-cover — a hard constraint on this change staying doc-only.
