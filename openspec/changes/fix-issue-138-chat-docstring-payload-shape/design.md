## Context

`get_chat_response` in `src/interfaces/chat_app/app.py` is the chat-app HTTP endpoint. Its
docstring is the only integrator-facing description of the request contract. The docstring
(around `:4620-4634`) describes `last_message` as a flat two-element list
(`["User", "hello"]`), but the request path consumes a list of pairs:

- `_parse_chat_request` (`app.py:4590`) maps `payload.get("last_message")` → `message`
  (`app.py:4606`).
- `_prepare_chat_context` (`app.py:1618`) does `sender, content = tuple(message[0])`
  (`app.py:1633`) — it takes the **first element** of `last_message` and unpacks it as
  `(sender, content)`.

So the accepted shape is `[["User", "hello"]]`. A client built from the docstring sends the
flat shape, `message[0]` is `"User"`, `tuple("User")` yields four characters, and the route
raises `ValueError: too many values to unpack (expected 2)` → HTTP 500 (reproduced live
2026-07-22 against dev `a8e06c79`). Both in-repo clients already send the nested shape:
`static/chat.js:266` (`last_message: history.slice(-1)` over `[sender, content]` pairs) and
`openai_compat.py:242` (`last_message = [("user", query)]`).

**Hard constraint:** `src/interfaces/chat_app/app.py` is not imported by any unit test, so
any new *executable* line there fails the ≥80% diff-cover gate (patch coverage vs
`origin/dev`). A docstring-only edit adds no executable lines and is safe.

## Goals / Non-Goals

**Goals:**
- Make the `get_chat_response` docstring describe the `last_message` shape the handler
  actually accepts, with a concrete nested example, so an integrator building from the
  docstring produces a request that succeeds.
- Keep the change docstring-only so it passes the gate unchanged (no diff-cover exposure).

**Non-Goals:**
- No runtime behavior change. The handler continues to read `message[0]` exactly as today.
- No payload validation / 400-on-malformed handling. That is a real, defensible follow-on
  but is a behavior change in a coverage-trapped file and MUST be a separate PR routed
  through a tested helper module. It is explicitly excluded here.
- No changes to the in-repo clients (they already send the correct shape).

## Decisions

**Decision: Fix the documentation, not the code.** The endpoint's accepted shape is the
one both real clients already send and the one `_prepare_chat_context` unpacks; that is the
de-facto contract. The docstring is the artifact that is wrong. Aligning the docstring to
the code (rather than loosening the code to accept the flat shape) preserves existing
callers and avoids touching a coverage-trapped file.
- *Alternative considered — make the handler also accept the flat shape:* rejected. It adds
  executable lines to `app.py` (fails diff-cover), changes runtime behavior, and creates
  two valid shapes for one field, which is worse for integrators than one documented shape.
- *Alternative considered — add 400 validation now:* rejected for this change; deferred to a
  separate PR per the issue's scope decision.

**Decision: Edit only the `last_message` description lines** (around `:4626-4628`), keeping
the surrounding docstring style. State that `last_message` is a list containing a single
`[sender, message]` pair, give the example `[["User", "How do I submit a job?"]]`, and note
that only the first pair is read.

## Risks / Trade-offs

- **[The docstring still won't stop a malformed request from 500-ing]** → Accepted for this
  change; the 400-validation follow-on is tracked separately. Correcting the docs removes
  the most common cause (integrators copying the wrong shape).
- **[An accidental executable-line edit would fail the gate]** → Mitigation: the acceptance
  check `git diff origin/dev -- src/interfaces/chat_app/app.py` must show only
  docstring/comment lines; the gate (diff-cover) is the backstop and must exit 0.
- **[Docstring could drift from code again]** → Mitigation: the spec ties the documented
  example to the shape unpacked at `app.py:1633`, so future reviewers have a written
  contract to check against.

## Migration Plan

None. Documentation-only; no deploy, data, or API-behavior change. Rollback is reverting the
single docstring edit.
