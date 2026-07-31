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

**Coverage note (corrected 2026-07-31 — the original claim here was wrong):**
`src/interfaces/chat_app/app.py` *is* imported by unit tests — `test_chat_override_concurrency.py:27`,
`test_chat_override_persistence.py:32-33`, `test_provider_config_override.py:12` and
`test_request_local_pipeline.py:21` — and the module measures 14% line coverage. Diff
coverage is also computed per changed *executable line*, not per file, so importing a large
module says nothing about whether a given new line is covered.

The narrower statement that is actually true: the **request path** through this module is
not exercised. Importing the module executes its `def` statements, so signature lines
register as covered, but the bodies of `_prepare_chat_context` (the `tuple(message[0])`
unpack at `app.py:1633`) and `get_chat_response` are not reached by any unit test. New
executable lines *there* would therefore land uncovered and drag the patch-coverage ratio
down, which is why the payload validation below is deferred to a separate PR routed through
a testable helper.

This is a statement about where the tests currently reach, not a prohibition. A future
contributor may add executable lines to this file, and should — provided they bring the
coverage with them. A docstring-only edit adds no executable lines either way.

## Goals / Non-Goals

**Goals:**
- Make the `get_chat_response` docstring describe the `last_message` shape the handler
  actually accepts, with a concrete nested example, so an integrator building from the
  docstring produces a request that succeeds.
- Publish the same contract in `docs/docs/api_reference.md`, because the repository requires
  user-facing API changes to be documented there and an integrator is far likelier to read
  that page than a docstring. This is where the request-body table, the `last_message`
  nesting rule, the two required timing fields, and the stream-only override fields live.
- Keep the change **documentation-only** — no executable-code edit, so no diff-cover
  exposure. "Documentation" covers both the docstring and the API reference page; earlier
  revisions of this design said "docstring-only", which understated the scope actually
  shipped.

**Non-Goals:**
- No runtime behavior change. The handler continues to read `message[0]` exactly as today.
- No payload validation / 400-on-malformed handling. That is a real, defensible follow-on,
  but it is a behavior change on a request path no unit test currently reaches, so it needs
  a separate PR that routes the check through a testable helper and brings its own coverage.
  It is explicitly excluded here.
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

None. Documentation-only; no deploy, data, or API-behavior change. Rollback is reverting
both documentation edits — the `get_chat_response` docstring in
`src/interfaces/chat_app/app.py` and the request-contract section of
`docs/docs/api_reference.md`. Neither carries runtime behaviour, so a revert is safe in
either order and needs no redeploy.
