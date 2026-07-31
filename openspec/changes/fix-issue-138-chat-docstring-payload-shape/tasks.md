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

## 4. Ship (post-implementation, handled by the loop)

- [x] 4.1 Commit on `fix/issue-138-chat-docstring-payload-shape` (short lowercase message,
  no `Co-Authored-By` trailer); the pre-commit gate must pass without `--no-verify`.
- [x] 4.2 Open the PR to `fasrc/archi:dev` (`gh pr create --repo fasrc/archi --base dev`),
  linking `closes #138`, then post an `@codex review` comment. Do NOT merge.
