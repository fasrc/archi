## 1. Confirm the premise

- [x] 1.1 Re-read `src/interfaces/chat_app/app.py:1635-1658` and confirm the three history-source
  branches, the refresh trim at `:1650-1652`, and the skipped append at `:1657-1658`.
- [x] 1.2 Confirm the two duplicated error-message chains at `:2019-2025` (streaming) and
  `:4668-4674` (non-streaming route) know only `408` and `403`.
- [x] 1.3 Confirm no in-repo client sends the rejected combination: `openai_compat.py:272` sends
  `is_refresh: False`; `static/script.js:781`/`:826` send `is_refresh` with a `conversation_id`.

## 2. RED — the defect and the boundaries around it

- [x] 2.1 New `tests/unit/test_chat_refresh_context.py`. Failing test: `is_refresh=True`,
  `conversation_id=None`, `external_history=None` returns `(None, 400)`. Drive
  `ChatWrapper._prepare_chat_context` with a stub `self`, stubbing `create_conversation`,
  `query_conversation_history` and `update_conversation_timestamp`. Pass a
  `client_sent_msg_ts`/`client_timeout` pair that satisfies the check at `:1654` — that unguarded
  comparison is #175 and must not be tripped or fixed here.
- [x] 2.2 Failing test: the same request calls `create_conversation` **zero** times.
- [x] 2.3 Guard test (must pass before *and* after): a refresh with `external_history` and no
  `conversation_id` is **not** rejected, and its trailing assistant turns are trimmed. This is the
  test that stops the guard from being written as "reject when `conversation_id is None`".
- [x] 2.4 Guard test: refresh **with** a `conversation_id` still trims trailing assistant turns and
  still does not append the incoming message.
- [x] 2.5 Guard test: `is_refresh=False` with no `conversation_id` still creates a conversation and
  appends the message.

## 3. GREEN — the guard

- [x] 3.1 Add the precondition immediately after `sender, content = tuple(message[0])` (`:1633`) and
  before `if external_history is not None:` (`:1635`):
  `if is_refresh and conversation_id is None and external_history is None: return None, 400`.
  Comment it with *why* the condition names both fields.
- [x] 3.2 Run 2.1–2.5 until green.

## 4. RED+GREEN — the shared error message

- [x] 4.1 Failing tests for a new module-level `_chat_error_message(error_code)`: `400` returns text
  naming the unsatisfiable refresh; `408` and `403` return their existing strings verbatim; any
  other status returns the generic server-error text.
- [x] 4.2 Implement it and replace both duplicated chains (`:2019-2025`, `:4668-4674`) with a call.
  The existing `408`/`403` strings must be reproduced exactly — assert on the constants, not on
  retyped literals.
- [x] 4.3 Assert the streaming path emits `{"type": "error", "status": 400, "message": ...}` with the
  new message, using the existing wrapper harness in `tests/unit/test_chat_override_persistence.py`
  as the model.

## 5. Mutation-check (non-vacuity)

- [x] 5.1 Delete the guard → 2.1 and 2.2 fail, and nothing else.
- [x] 5.2 Narrow the guard to `is_refresh and conversation_id is None` → 2.3 fails. This is the
  specific regression the change exists to avoid.
- [x] 5.3 Make `_chat_error_message` return the generic text for `400` → 4.1 and 4.3 fail.
- [x] 5.4 Restore and confirm green.

## 6. Docs

- [x] 6.1 Update the `is_refresh` row in `docs/docs/api_reference.md`. It currently documents the
  broken behaviour (new empty conversation, message dropped, pipeline invoked with no user turn);
  replace it with the `400` and the precise precondition — a refresh needs a `conversation_id` **or**
  `external_history`.
- [x] 6.2 Add `400` to the page's error-status coverage for both endpoints, consistent with the
  existing two-error-channels section: a real HTTP `400` from `POST /api/get_chat_response`, and an
  in-band `{"type": "error", "status": 400}` under HTTP 200 from the streaming endpoint, since
  `_prepare_chat_context` is called inside the generator.
- [x] 6.3 `cd docs && mkdocs build --strict` exits 0, and every in-page anchor still resolves.

## 7. Gate and ship

- [x] 7.1 `openspec validate fix-issue-177-refresh-requires-history --strict` passes.
- [ ] 7.2 `bash scripts/gate.sh` green through the pre-commit hook, **≥80% diff coverage on changed
  lines**; never `--no-verify`.
- [x] 7.3 `git diff origin/dev -- src/interfaces/chat_app/app.py` shows no unrelated black reflow.
- [ ] 7.4 Adversarial review on the branch; address what holds, push back with reasons on what does
  not.
- [ ] 7.5 Open the PR to `fasrc/archi:dev` with `closes #177`, then request `@codex review`. Do NOT
  merge.

## 8. Line anchors (unplanned — surfaced while updating the docs)

- [x] 8.1 The guard and the shared-message helper shift every line below `app.py:263`, which
  invalidates **every** `app.py#L<n>` link on `docs/docs/api_reference.md` — 31 of them. Remap
  mechanically rather than by hand: build an exact old→new line map from
  `difflib.SequenceMatcher` opcodes over `origin/dev`'s `app.py` versus the branch's, then
  rewrite the URL anchors, the `app.py:<n>` display text and the short `` `:<n>` `` form.
  Content-matching is not sufficient — lines like `)` and `if include_tool_steps:` occur dozens
  of times, so only a positional diff resolves them.
- [x] 8.2 One line does not map, because this change rewrote it: the streaming error `yield`
  (old `:2024`). Point it at the new `yield {` by hand.
- [x] 8.3 Verify every remapped anchor resolves to a plausible source line, printing the target
  line's content for each. This caught a **double-mapping** bug — the range anchor
  `#L4595-L4596` was remapped by the range pass and then remapped *again* by the single-number
  pass, yielding the nonsense `#L4669-L4633`.
- [x] 8.4 Two anchors authored in #159 pointed at a blank line (`[ovrguard]`) and a closing
  paren (`[ovrwarn]`). Corrected to the `if (` and the `yield` they were describing.
- [x] 8.5 **`[modelused]` was used but never defined** — it shipped that way in #159 and renders
  as a literal `[modelused]` on `dev` today. Added the definition. The round-8 check that should
  have caught it grepped a hand-written list of the refs I remembered adding; the check is now
  generic — *any* `[label]` not followed by `(` is an unresolved reference — and reports none.
