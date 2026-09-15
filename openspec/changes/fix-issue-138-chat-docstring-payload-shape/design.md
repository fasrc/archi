## Context

`get_chat_response` in `src/interfaces/chat_app/app.py` is the chat-app HTTP endpoint. Its
docstring was, before this change, the only integrator-facing description of the request
contract; this change also publishes it in `docs/docs/api_reference.md`. The docstring
(around `:4620-4634`) describes `last_message` as a flat two-element list
(`["User", "hello"]`), but the request path consumes a list of pairs:

- `_parse_chat_request` (`app.py:4590`) maps `payload.get("last_message")` → `message`
  (`app.py:4606`).
- `_prepare_chat_context` (`app.py:1618`) does `sender, content = tuple(message[0])`
  (`app.py:1633`) — it takes the **first element** of `last_message` and unpacks it as
  `(sender, content)`.

So the accepted shape is `[["User", "hello"]]`. A client built from the docstring sends the
flat shape, `message[0]` is `"User"`, `tuple("User")` yields four characters, and the route
raises `ValueError: too many values to unpack (expected 2)` → HTTP 500 from
`POST /api/get_chat_response` (reproduced live 2026-07-22 against dev `a8e06c79`). On the
streaming endpoint the same exception is raised inside the generator and surfaces in-band as
`{"type": "error", "status": 500}` under HTTP 200. Both in-repo clients already send the nested shape:
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
executable lines *there* would therefore land uncovered unless the change brings tests that
reach them, which is why the payload validation below is deferred to a separate PR. That PR
must budget for the test work; it is **not** required to adopt any particular design to avoid
it.

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
  but it is a behavior change on a request path no unit test currently reaches, so it needs a
  separate PR that brings its own coverage. How it gets that coverage — a testable helper, or a
  direct test of the request path — is the follow-up's call, not this change's. It is
  explicitly excluded here.
- No changes to the in-repo clients (they already send the correct shape).

## Decisions

**Decision: Fix the documentation, not the code.** The endpoint's accepted shape is the
one both real clients already send and the one `_prepare_chat_context` unpacks; that is the
de-facto contract. The docstring is the artifact that is wrong. Aligning the docstring to
the code (rather than loosening the code to accept the flat shape) preserves existing
callers, and keeps the change free of executable edits — which is a scope choice, not a
coverage prohibition (see the corrected note above and the alternative below).
- *Alternative considered — make the handler also accept the flat shape:* rejected, but **not**
  on coverage grounds. An earlier revision of this line said it "fails diff-cover", which
  contradicts the corrected coverage note above: diff coverage is computed per changed
  executable line, so new lines here are perfectly permissible *provided the change brings
  tests that reach them*. The real reasons stand on their own — it changes runtime behavior, and
  it creates two valid shapes for one field, which is worse for an integrator than one
  documented shape. Coverage is a cost to budget for, not a prohibition, and stating it as a
  prohibition would misdirect the deferred validation work toward a helper-only design when a
  direct test of the request path is equally available.
- *Alternative considered — add 400 validation now:* rejected for this change; deferred to a
  separate PR per the issue's scope decision.

**Decision: in `app.py`, edit only the `last_message` description lines** (around
`:4626-4628`), keeping the surrounding docstring style. State that `last_message` is a list
containing a single `[sender, message]` pair, give the example
`[["User", "How do I submit a job?"]]`, and note that only the first pair is read.

**Decision: also publish the contract in `docs/docs/api_reference.md`** — recorded here as a
decision, not just as a goal, because the earlier revision of this section said "edit only the
three docstring lines" while the change ships ~300 lines to that page. The repository requires
user-facing API changes to be documented in `docs/`, and an integrator is far likelier to read
that page than a docstring, so the page is part of the deliverable rather than an extra.

Scoping it explicitly matters for **rollback**: reverting this change means reverting both
edits, and the migration plan says so. It also bounds the page — it documents the request
contract (shape, timing fields, override behaviour, error channels, stream events) and is not
a general rewrite of the API reference. Review rounds 3–8 expanded that section considerably;
the boundary above is what keeps "document the contract" from becoming "document the handler".

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
