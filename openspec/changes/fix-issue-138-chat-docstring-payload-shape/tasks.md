## 1. Locate the docstring

- [x] 1.1 Read `src/interfaces/chat_app/app.py:4620-4634` — the `get_chat_response`
  docstring — and confirm the `last_message` description (around `:4626-4628`) still says
  a flat "list of length 2" (`["User", "hello"]`).
- [x] 1.2 Confirm the real contract at `app.py:1633` (`sender, content = tuple(message[0])`)
  and the two in-repo clients (`static/chat.js:266`, `openai_compat.py:242`) send the nested
  `[[sender, content]]` shape — this is the shape the docstring must describe.

## 2. Fix the docstring (doc-only)

- [x] 2.1 Rewrite the `last_message` description so it states `last_message` is a list
  containing a single `[sender, message]` pair, gives the concrete example
  `[["User", "How do I submit a job?"]]`, and notes that only the first pair is read.
  Keep the surrounding docstring style. Do NOT describe it as a flat "list of length 2".
- [x] 2.2 Make no **executable-code** change: no payload validation, no 400-on-malformed
  handling (that is a separate PR, out of scope here). Documentation edits outside the
  docstring are in scope — see 2.3.
- [x] 2.3 Publish the same contract in `docs/docs/api_reference.md`, as the repository
  requires for user-facing API changes: the request-body table, the `last_message` nesting
  rule with the failure modes of the flat form, the two timing fields that are required in
  practice (omitting either returns HTTP 408, tracked as #175), and the four override fields
  that only the streaming endpoint honours.

## 3. Verify against acceptance criteria

- [x] 3.1 `sed -n '4620,4634p' src/interfaces/chat_app/app.py` no longer describes
  `last_message` as a flat "list of length 2" and shows a concrete `[["User", ...]]` example.
- [x] 3.2 `git diff origin/dev -- src/interfaces/chat_app/app.py` touches only
  docstring/comment lines (no executable-code change).
- [x] 3.3 Run the gate in the full-deps env and confirm it exits 0:
  `source ~/miniforge3/etc/profile.d/conda.sh && conda activate archi && bash scripts/gate.sh`
  (a documentation-only edit adds no executable lines, so diff-cover has nothing to fail on).
- [x] 3.4 The published example in `docs/docs/api_reference.md` is a request that actually
  succeeds — it includes `client_sent_msg_ts` and `client_timeout`, without which the handler
  returns HTTP 408. Verify the field table marks both as required in practice, and marks
  `provider` / `model` / `include_agent_steps` / `include_tool_steps` as stream-only.
- [x] 3.5 The runnable example **generates** `client_sent_msg_ts` instead of hard-coding an
  epoch literal. A literal passes review on the day it is written and silently rots: the
  check at `app.py:1654` compares it to the server clock, so once it is older than
  `client_timeout` every copied request is rejected. Any non-runnable shape template uses an
  unquoted placeholder so a blind paste fails in the caller's own JSON parser rather than
  arriving as a non-integer and surfacing as an opaque HTTP 500.
- [x] 3.6 The page distinguishes how the two endpoints report the rejection: HTTP 408 for
  `POST /api/get_chat_response`, versus HTTP **200** plus a `meta` line and then an
  `{"type": "error", "status": 408}` NDJSON event for the streaming endpoint
  (`app.py:2024`). A streaming client checking only the HTTP status must be warned it will
  read a failure as success.
- [x] 3.7 The page states that `provider` and `model` are **jointly required** — the override
  is gated on `if provider and model` (`app.py:2037`), so one without the other silently
  leaves the default pipeline in place.
- [x] 3.8 The HTTP-200 warning is **scoped to failures raised after the stream opens**. The
  boundary is the `Response(stream_with_context(...))` construction at `app.py:4768`, not the
  kind of error: `require_auth` returns 401 (or a 302 redirect to login) before the route
  body runs, and a missing `client_id` returns 400 at `app.py:4730` — all three with no
  `meta` line and no `error` event. An unscoped "errors arrive as events, not status codes"
  tells a client to disregard status codes, which turns a 401 into an apparently-successful
  stream. The page must direct callers to check the status **and** inspect events.
- [x] 3.9 The step flags are described by the events they gate, not by their names:
  `include_agent_steps` gates the answer `chunk` events (`app.py:2365`, `:2399`);
  `include_tool_steps` gates the tool events **and** `thinking_start` / `thinking_end`
  (`app.py:2345`, `:2359`). The page warns that setting `include_agent_steps: false` to hide
  reasoning both fails to hide it and silently drops streamed answer text, and that the
  symptom is a late-arriving `final` answer rather than an error.
- [x] 3.10 The override is documented as *attempted* rather than applied, and `warning` is added
  to the streaming event-type table. **Superseded by 3.13** — this task originally said "all
  three outcomes" and then described a fourth, which is the closed-enumeration defect 3.12 was
  written to remove. The outcomes are not a fixed count: they are grouped by how the caller
  finds out (`error` + stream ends, `warning` + fallback, silence, in-band `500` at invocation),
  and 3.13 is the current statement of that.
- [x] 3.11 The runnable example states its authentication precondition. Every chat route is
  wrapped in `require_auth` (`app.py:2729`), so with `auth.enabled: true` the command gets
  `401`/`302` and never reaches the handler — "a request that succeeds" holds only where auth
  is disabled or a session cookie is passed. The basic-auth cookie flow is shown
  (`/login` takes form-encoded `username`/`password`, `app.py:3213`) and the SSO case is named
  as not completable with `curl`.
- [x] 3.12 Replace the page's **closed enumerations with open ones**, and say so up front. Four
  of round 5's five findings were edges of exhaustive claims added in rounds 3–4 ("three
  outcomes", "the only silent case", a closed gated-event list, an endpoint-neutral HTTP 500):
  a closed list about a handler whose behaviour varies by pipeline and provider is a hostage to
  the next edge case. Document the deciding *mechanism* plus non-exhaustive examples, tell
  clients to tolerate unseen event types and statuses, and record the property in the spec so
  re-tightening a list is a visible regression rather than an improvement.
- [x] 3.13 Override outcomes are organized by **how the caller finds out**, and include the two
  silent paths: `_create_provider_llm` returning falsey rather than raising (what an
  `ImportError` does, `app.py:1611`) and no `agent_llm` on the active pipeline (`app.py:2055`).
  Drop the claim that an unknown model ID yields `400` — `get_chat_model` does not check the
  provider catalogue, so for OpenAI/OpenRouter it fails at invocation as an in-band
  `{"type": "error", "status": 500}` (`app.py:2568`). Scope the `400` to construction-time
  `ValueError`.
- [x] 3.14 `include_tool_steps` also gates the legacy `step` events non-agent pipelines emit
  (`app.py:2386` → `:1701`); add them to the flag's categories and add `step` to the
  event-type table with its `step_type` values, so a client built from the table does not
  discard tool updates from a supported pipeline.
- [x] 3.15 `is_refresh` is marked as requiring `conversation_id`. Without one the handler opens
  a new conversation with empty history (`app.py:1639`) and then skips appending the message
  (`app.py:1657`), so the pipeline is invoked with no user turn at all — it is not an
  independent switch.
- [x] 3.16 The flat-`last_message` failure is split by endpoint: HTTP 500 for
  `POST /api/get_chat_response`, in-band `{"type": "error", "status": 500}` under HTTP 200 for
  the streaming endpoint (`app.py:2568`). The two-character-sender case succeeds silently on
  both.

## 4. Ship (post-implementation, handled by the loop)

- [x] 4.1 Commit on `fix/issue-138-chat-docstring-payload-shape` (short lowercase message,
  no `Co-Authored-By` trailer); the pre-commit gate must pass without `--no-verify`.
- [x] 4.2 Open the PR to `fasrc/archi:dev` (`gh pr create --repo fasrc/archi --base dev`),
  linking `closes #138`, then post an `@codex review` comment. Do NOT merge.
